//! MIA; But AI got you — Tauri 데스크톱 셸.
//!
//! # 이 셸이 하는 일은 세 가지뿐이다
//!
//! 1. Python 백엔드를 자식 프로세스로 띄운다
//! 2. `/api/health` 가 응답할 때까지 기다린다
//! 3. 그 주소를 가리키는 창을 만든다
//!
//! 앱을 닫으면 자식 프로세스를 죽인다. 그게 전부다.
//!
//! # 왜 정적 파일을 번들하지 않고 백엔드 URL 을 직접 여는가
//!
//! 화면(`src/mesh/web/`)을 Tauri 에 번들하면 origin 이 `tauri://localhost` 가
//! 되고, `fetch("/api/...")` 가 백엔드에 도달하지 못한다. 절대 URL 로 바꾸면
//!
//!   - `connect-src` 를 열어야 하고
//!   - 화면 코드에 호스트가 하드코딩되고
//!   - `lint_web.py` 의 "외부 URL 0건" 규칙이 깨진다
//!
//! 백엔드 주소를 그대로 열면 **같은 origin** 이 되어 FastAPI 가 설정한 CSP·
//! 보안 헤더가 그대로 적용된다. 브라우저에서 열 때와 완전히 같은 코드 경로다.
//! 데스크톱 셸이 보안 속성을 바꾸지 않는 것이 핵심이다.
//!
//! # 환경변수
//!
//! | 변수 | 기본값 | 뜻 |
//! |---|---|---|
//! | `MESH_APP_URL` | `http://127.0.0.1:8080` | 창이 열 주소 |
//! | `MESH_BACKEND_CMD` | `make run` | 백엔드 기동 명령 |
//! | `MESH_BACKEND_CWD` | 저장소 루트 추정 | 명령 실행 디렉터리 |
//! | `MESH_SKIP_BACKEND` | (없음) | `1` 이면 기동하지 않고 붙기만 한다 |

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

/// 백엔드가 뜰 때까지 기다리는 최대 시간.
///
/// 첫 실행은 `uv sync` 가 의존성을 내려받을 수 있어 넉넉해야 한다.
/// 이 시간을 넘기면 창을 띄우지 않고 실패를 알린다 — 빈 창을 보여주고
/// "왜 안 되지?" 하게 만드는 것보다 낫다.
const READY_TIMEOUT: Duration = Duration::from_secs(120);
const POLL_INTERVAL: Duration = Duration::from_millis(250);

const DEFAULT_URL: &str = "http://127.0.0.1:8080";
const DEFAULT_CMD: &str = "make run";

/// 자식 프로세스를 붙잡아 두고 종료 시 죽인다.
struct Backend(Mutex<Option<Child>>);

impl Backend {
    fn shutdown(&self) {
        if let Ok(mut guard) = self.0.lock() {
            if let Some(mut child) = guard.take() {
                // 정상 종료를 기다리지 않는다. 이 프로세스는 로컬 개발 서버이고
                // 상태는 SQLite 파일에 이미 커밋되어 있다.
                let _ = child.kill();
                let _ = child.wait();
                eprintln!("[mesh] 백엔드를 종료했습니다");
            }
        }
    }
}

fn env_or(key: &str, fallback: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| fallback.to_string())
}

/// 저장소 루트를 찾는다. `Makefile` 과 `src/mesh` 가 함께 있는 첫 조상.
///
/// `cargo run` 은 `src-tauri/` 에서 돌고 번들된 앱은 전혀 다른 곳에서 돌기
/// 때문에, 실행 위치를 가정하지 않고 위로 훑는다.
fn find_repo_root() -> Option<PathBuf> {
    if let Ok(explicit) = std::env::var("MESH_BACKEND_CWD") {
        let path = PathBuf::from(explicit);
        if path.is_dir() {
            return Some(path);
        }
    }
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(cwd) = std::env::current_dir() {
        candidates.push(cwd);
    }
    if let Ok(exe) = std::env::current_exe() {
        candidates.push(exe);
    }
    for start in candidates {
        let mut cursor: Option<&std::path::Path> = Some(start.as_path());
        while let Some(dir) = cursor {
            if dir.join("Makefile").is_file() && dir.join("src").join("mesh").is_dir() {
                return Some(dir.to_path_buf());
            }
            cursor = dir.parent();
        }
    }
    None
}

/// `http://host:port` 에서 `host:port` 만 뽑는다.
fn authority(url: &str) -> String {
    let rest = url.split("://").nth(1).unwrap_or(url);
    let host = rest.split('/').next().unwrap_or(rest);
    if host.contains(':') {
        host.to_string()
    } else {
        format!("{host}:80")
    }
}

/// TCP 연결이 되는지만 본다.
///
/// HTTP 요청을 보내지 않는 이유: HTTP 클라이언트를 의존성으로 추가하지 않으려는
/// 것이다. 포트가 열렸다면 uvicorn 이 떴다는 뜻이고, 그 뒤는 웹뷰가 확인한다.
fn is_up(addr: &str) -> bool {
    TcpStream::connect_timeout(
        &match addr.parse() {
            Ok(parsed) => parsed,
            Err(_) => return false,
        },
        Duration::from_millis(400),
    )
    .is_ok()
}

fn spawn_backend(root: &PathBuf, cmd: &str) -> std::io::Result<Child> {
    eprintln!("[mesh] 백엔드 기동: {cmd}  (cwd: {})", root.display());
    let mut child = Command::new("/bin/sh")
        .arg("-lc")
        .arg(cmd)
        .current_dir(root)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;

    // 백엔드 로그를 그대로 흘려보낸다. 실패 원인을 사용자가 볼 수 있어야 한다 —
    // 이 프로젝트에서 조용한 실패는 금지다.
    for (name, stream) in [
        ("out", child.stdout.take().map(|s| Box::new(s) as Box<dyn std::io::Read + Send>)),
        ("err", child.stderr.take().map(|s| Box::new(s) as Box<dyn std::io::Read + Send>)),
    ] {
        if let Some(stream) = stream {
            std::thread::spawn(move || {
                for line in BufReader::new(stream).lines().map_while(Result::ok) {
                    eprintln!("[backend:{name}] {line}");
                }
            });
        }
    }
    Ok(child)
}

fn main() {
    let url = env_or("MESH_APP_URL", DEFAULT_URL);
    let addr = authority(&url);
    let skip = std::env::var("MESH_SKIP_BACKEND").is_ok_and(|v| v == "1");

    tauri::Builder::default()
        .manage(Backend(Mutex::new(None)))
        .setup(move |app| {
            let already_running = is_up(&addr);
            if already_running {
                eprintln!("[mesh] 이미 {addr} 에서 실행 중입니다. 그 서버에 붙습니다");
            } else if skip {
                eprintln!("[mesh] MESH_SKIP_BACKEND=1 — 기동하지 않습니다");
            } else {
                let root = find_repo_root().ok_or_else(|| {
                    "저장소 루트를 찾지 못했습니다. MESH_BACKEND_CWD 를 지정해 주세요".to_string()
                })?;
                let child = spawn_backend(&root, &env_or("MESH_BACKEND_CMD", DEFAULT_CMD))?;
                app.state::<Backend>().0.lock().unwrap().replace(child);
            }

            // 준비될 때까지 기다린다. 빈 창을 먼저 띄우고 실패를 숨기지 않는다.
            if !already_running && !skip {
                let started = Instant::now();
                while !is_up(&addr) {
                    if started.elapsed() > READY_TIMEOUT {
                        return Err(format!(
                            "백엔드가 {}초 안에 응답하지 않았습니다 ({addr}). \
                             터미널에서 `make preflight` 로 환경을 확인해 주세요",
                            READY_TIMEOUT.as_secs()
                        )
                        .into());
                    }
                    std::thread::sleep(POLL_INTERVAL);
                }
                eprintln!("[mesh] 준비됨 ({:.1}초)", started.elapsed().as_secs_f32());
            }

            let parsed = url.parse().map_err(|_| format!("잘못된 URL: {url}"))?;
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(parsed))
                .title("MIA; But AI got you")
                .inner_size(1180.0, 860.0)
                .min_inner_size(760.0, 560.0)
                .resizable(true)
                .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Tauri 앱을 만들지 못했습니다")
        .run(|handle, event| {
            if let tauri::RunEvent::Exit = event {
                handle.state::<Backend>().shutdown();
            }
        });
}
