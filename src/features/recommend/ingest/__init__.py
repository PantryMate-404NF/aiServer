"""재료명 정규화 파이프라인 (설계 ④).

P1 전처리 → P2 분해 → P3 매칭 캐스케이드 → P4 역할 판정까지 구현됐습니다.
P5 수량환산은 미착수입니다.

02 의 7.3 — **배럴은 도메인 진입점 하나뿐입니다.** 여기서 re-export 하지
않습니다. 쓰는 쪽이 모듈을 직접 가리킵니다.

    from features.recommend.ingest.parse import normalize
    from features.recommend.ingest.match import Dictionary, match
"""
