# scripts/reco/bench/ — 설계 판단의 근거가 된 시뮬레이션

> 작성자: 박재우 · 작성일: 2026-09-03

문서에 인용된 숫자를 **재현할 수 있게** 저장소에 남긴다.
"측정했다"고 쓰고 코드가 없으면 6개월 뒤 아무도 확인할 수 없다.

| 파일 | 무엇을 재는가 | 문서 |
|---|---|---|
| `q3_linear_vs_gam.py` | 선형 vs GAM vs 도메인변환 — 쌍대비교 라벨 수별 NDCG@10 | 01 5-2-6 |
| `serendipity_sim.py` | 탐색 전략 6종 — 숨은 취향 발견 · 조리 수 · propensity | 01 5-3-5 |
| `serendipity_strategies.py` | 거리 기반 vs 품질하한 vs Thompson · 탐색 슬롯 수 스윕 | 01 5-3-5 |
| `serendipity_mix.py` | 혼합 정책 · Thompson propensity 의 MC 계산 비용 | 01 5-3-5 |
| `kmeans_worth_it.py` | **k-means 배치를 빼면 무엇이 꺼지는가** — 세션 수별 | 01 5-3-5 · 04 컷라인 |
| `taste_axes.py` | 맛 축을 몇 개로 — 축 수 vs 노이즈 | 01 2-5-1 ① |
| `merge_vs_separate.py` | 맛·나라·카테고리를 합칠까 따로 둘까 | 01 2-5-1 ② |
| `vector_vs_scalar.py` | 코사인 vs 축별가중 vs 완전이중선형 | 01 2-5-1 ③ |
| `capacity_curve.py` | **모델 크기 vs 라벨 수** — 최적점 곡선 | 01 2-5-1 ④ · 5-2-6 |

`q3_linear_vs_gam.py` 는 **numpy 필수**(`uv pip install --python .venv/bin/python numpy`),
serendipity·kmeans 4종은 순수 파이썬이다.

> ⚠️ 한때 이 표에 있던 `q3_realism_probe`·`q3_run`·`q3_cross`·`q3_active` 4종은
> 폭주 정리 때 삭제됐다. 그 검증(가정 스위치·능동선택)의 생존 결론은
> `q3_linear_vs_gam.py` 의 `Q3TEMP`·`Q3MODE` 스위치로 흡수됐고, 흡수 안 된 수치는
> 01 5-2-6 에 "세션 기록 기준"으로만 남는다.

```bash
.venv/bin/python scripts/reco/bench/kmeans_worth_it.py
```

## 🔴 읽는 법

**절대 수치는 시뮬레이션 가정에 의존한다. 견고한 것은 순서이지 소수점이 아니다.**
각 스크립트 상단 docstring 에 가정을 적어두었다.

그리고 `q3_linear_vs_gam.py` 에는 **자기정정의 기록**이 들어 있다 —
`cheat` 는 정답 함수를 알고 만든 변환이고 `blind` 는 모르고 만든 것이다.
처음 결론은 `cheat` 로 냈고, 그것이 틀렸음을 `blind` 가 드러냈다.

## 민감도 확인

```bash
Q3TEMP=0.05 .venv/bin/python scripts/reco/bench/q3_linear_vs_gam.py --quick   # 라벨이 일관적일 때
Q3TEMP=0.20 .venv/bin/python scripts/reco/bench/q3_linear_vs_gam.py --quick   # 시끄러울 때
Q3MODE=ablate .venv/bin/python scripts/reco/bench/q3_linear_vs_gam.py --quick # 이득의 원천 가르기
```

`temp` 는 **저장소 어디에도 근거가 없는 자유 상수**이고, 5-2-6 의 결론이 여기 가장 크게
좌우된다. I-13 쌍대비교 수집 시 **쌍의 5%를 중복 출제해 재현율을 재면** 이 값이 나온다.
