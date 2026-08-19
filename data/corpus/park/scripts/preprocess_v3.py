#!/usr/bin/env python3
# title: 전처리 파이프라인 v3
# 보안등급: 사내
# as_of: 2026-08-19
# formality: official
# owner: person:park
"""atlas-ml 전처리 v3.

v2 대비 변경점:
  - 라벨 불균형 처리를 오버샘플링 단독에서 하이브리드로 변경
  - 파생 피처 3개 추가
  - 결측 처리를 median 에서 그룹별 median 으로

데이터셋: data/preproc_v3/  (고객 로그 파생. 취급 주의)
"""

from __future__ import annotations

import logging

import yaml
from atlas_ml.features import GroupMedianImputer, build_derived
from atlas_ml.io import load_raw, write_parquet
from atlas_ml.sampling import RandomOverSampler

log = logging.getLogger("atlas_ml.preprocess_v3")

CONFIG_PATH = "configs/v3.yaml"


def build_sampler(cfg: dict) -> RandomOverSampler:
    """라벨 불균형 처리 — 2단계 중 1단계.

    1:1 로 맞추지 않고 sampling_strategy=0.5 에서 멈춘다.
    나머지는 학습 시 class_weight 로 보정한다 (train.py 참조).

    오버샘플링만으로 1:1 을 만들면 소수 클래스가 과도하게 복제되어
    과적합이 심해진다. 0.5 에서 멈추고 가중치로 마무리하는 쪽이
    검증 재현율이 더 좋았다. (runs/ 아래 실험 로그 참조)
    """
    return RandomOverSampler(
        sampling_strategy=cfg.get("sampling_strategy", 0.5),
        random_state=cfg.get("random_state", 42),
    )


def main() -> None:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    df = load_raw(cfg["input_path"])
    log.info("loaded rows=%d", len(df))

    df = GroupMedianImputer(group_by=cfg["impute_group"]).fit_transform(df)
    df = build_derived(df, specs=cfg["derived_features"])

    sampler = build_sampler(cfg)
    x_res, y_res = sampler.fit_resample(df.drop(columns=["label"]), df["label"])
    log.info("resampled rows=%d strategy=%s", len(x_res), sampler.sampling_strategy)

    # 학습 쪽에서 쓰는 클래스 가중치. 여기서 계산해 config 로 넘긴다.
    class_weight = "balanced_subsample"
    log.info("class_weight=%s", class_weight)

    write_parquet(x_res.assign(label=y_res), cfg["output_path"])
    log.info("wrote %s", cfg["output_path"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
