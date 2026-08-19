.PHONY: help setup setup-infra preflight run test test-fast eval eval-classify \
        eval-dump-payloads lint audit bundle-lambda bootstrap deploy destroy \
        demo record-fixtures clean

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
	$(PY) uvicorn mesh.main:app --app-dir src \
		--host $${MESH_BIND_HOST:-127.0.0.1} --port $${MESH_BIND_PORT:-8080}

test:                     ## 단위 + 속성 + 인프라 어서션
	$(PY) pytest tests/unit tests/property -q

test-fast:                ## 단위만 (속성 테스트 제외)
	$(PY) pytest tests/unit -q

eval:                     ## 전체 평가 (분류 + 시나리오 + 유출 전수)
	$(PY) pytest tests/eval -q

eval-classify:            ## Day 2 게이트 — 기밀 재현율 100% 확인
	$(PY) pytest tests/eval/test_classification.py -q -s

eval-dump-payloads:       ## 육안 전수 확인용 페이로드 덤프
	$(PY) python scripts/dump_payloads.py

lint:
	$(PY) ruff check src tests scripts
	$(PY) ruff format --check src tests scripts
	@bash scripts/lint-web.sh

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

clean:
	rm -rf .pytest_cache .ruff_cache .hypothesis
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
