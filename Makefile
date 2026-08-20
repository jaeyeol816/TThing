.PHONY: help setup setup-infra preflight run app app-setup app-build \
        test test-fast eval eval-classify eval-dump-payloads e2e \
        lint audit bundle-lambda bootstrap deploy destroy \
        demo record-fixtures api-fixtures clean

PY := uv run

help:                     ## 사용 가능한 타깃
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

setup:                    ## 앱 의존성 + 데이터 디렉터리
	uv sync
	@test -f .env || (cp .env.example .env && echo ">> .env 생성됨 — FRIENDLI_TOKEN 을 기입하세요")
	@mkdir -p data/verified data/fixtures/exaone data/fixtures/agent data/fixtures/api
	@touch data/verified/.gitkeep
	@echo ">> setup 완료. 다음: make preflight"

setup-infra:              ## CDK 의존성 (앱과 별도 venv)
	cd infra && uv venv && uv pip install -r requirements.txt

preflight:                ## 환경 검증 — 다른 컴퓨터에서 가장 먼저 실행
	$(PY) python scripts/preflight.py

run:                      ## 로컬 앱 (127.0.0.1:8080)
	@# --factory: create_app() 은 팩토리다. 모듈 수준 `app` 을 두지 않는 이유는
	@# import 시점에 Config.load() 가 돌아 테스트가 환경변수에 묶이기 때문이다.
	$(PY) uvicorn --factory mesh.main:create_app --app-dir src \
		--host $${MESH_BIND_HOST:-127.0.0.1} --port $${MESH_BIND_PORT:-8080}

app-setup:                ## Tauri 셸 의존성 (Node + Rust 필요)
	cd app && npm install --no-audit --no-fund

app: app-setup            ## 데스크톱 앱 실행 (백엔드를 스스로 띄운다)
	@# 백엔드가 이미 떠 있으면 그 서버에 붙는다. 없으면 `make run` 을 자식으로
	@# 띄우고 포트가 열릴 때까지 기다린 뒤 창을 만든다 (app/src-tauri/src/main.rs).
	cd app && npm run tauri dev

app-build:                ## 배포용 .app / .dmg 빌드
	cd app && npm install --no-audit --no-fund && npm run tauri build

test:                     ## 단위 + 속성 + 인프라 어서션
	$(PY) pytest tests/unit tests/property -q

test-fast:                ## 단위만 (속성 테스트 제외)
	$(PY) pytest tests/unit -q

eval:                     ## 전체 평가 (분류 + 시나리오 + 유출 전수)
	$(PY) pytest tests/eval -q

eval-classify:            ## Day 2 게이트 — 기밀 재현율 100% 확인
	$(PY) pytest tests/eval/test_classification.py -q -s

eval-dump-payloads:       ## G4 — 육안 전수 확인용 페이로드 덤프
	@# 시나리오(업로드 포함)를 돌려 감사 DB 를 채운 뒤 덤프한다.
	@# --fresh 없이 돌리면 이전 실행 레코드까지 함께 덤프된다.
	$(PY) python scripts/dump_payloads.py --generate --fresh

e2e:                      ## 떠 있는 서버에 실제 HTTP 로 종단 실측 (업로드→질문)
	@# `make run` 이 live 모드로 먼저 떠 있어야 한다. 테스트가 아니라 실측 도구다.
	@# 매번 새 문서를 만들어 올리므로 목업 모드에서는 픽스처가 있을 수 없다 —
	@# 스크립트가 /api/health 로 확인하고 목업이면 즉시 멈춘다.
	$(PY) python scripts/e2e_upload_ask.py $(ARGS)

lint:
	$(PY) ruff check src tests scripts
	$(PY) ruff format --check src tests scripts
	$(PY) python scripts/lint_web.py

audit:                    ## 의존성 취약점 (SECURITY-10)
	$(PY) pip-audit

bundle-lambda:            ## U1 코드를 Lambda 번들로 복사 (deploy 가 의존)
	rm -rf infra/lambda/agent_broker/_bundled
	mkdir -p infra/lambda/agent_broker/_bundled
	cp src/mesh/validator.py src/mesh/schemas.py infra/lambda/agent_broker/_bundled/
	cp data/vocab.json infra/lambda/agent_broker/_bundled/

bootstrap:                ## CDK 부트스트랩 (Day 0, 한 번만)
	cd infra && npx aws-cdk@2 bootstrap aws://891401657794/us-east-1

deploy: bundle-lambda     ## 클라우드 배포
	cd infra && npx aws-cdk@2 deploy --all --require-approval never

destroy:
	cd infra && npx aws-cdk@2 destroy --all

demo:                     ## CLI 3막 재생 (화면 없이도 시연 가능)
	$(PY) python scripts/demo.py

record-fixtures:          ## live 로 3막 실행하며 목업 픽스처 녹화
	MESH_RECORD_FIXTURES=1 $(PY) python scripts/demo.py

record-dump-fixtures:     ## G4 덤프 경로(업로드 포함)의 픽스처 녹화
	@# `make eval-dump-payloads` 가 FixtureMissing 으로 죽으면 이걸 돌린다.
	@# 프롬프트에 들어가는 내용(코퍼스·가명화 목록·질문 문구)이 바뀌면
	@# 픽스처 키가 바뀐다 — 그게 설계다. 키가 내용에 묶여 있지 않으면
	@# 목업이 실물과 다른 것을 재생하고, 그걸 알 방법이 없다.
	MESH_RECORD_FIXTURES=1 EXAONE_MODE=live AGENT_TRANSPORT=direct \
		$(PY) python scripts/dump_payloads.py --generate --fresh

api-fixtures:             ## U4 화면 선행 개발용 API 목업 재생성 (실제 모델 기반)
	$(PY) python scripts/gen_api_fixtures.py

clean:
	rm -rf .pytest_cache .ruff_cache .hypothesis
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
