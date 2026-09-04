"""P3 보조 — 재료명을 `수식어* + 핵심어` 로 분해한다 (설계 4-4).

## 왜 퍼지 매칭이 실패했는가

4-4-1 실측: trgm 유사도로는 정탐 평균 0.143, 오탐 평균 0.118 로 분포가 겹쳐
**임계값 0.6 에서 재현율 0%** 였다. 자모 분해는 오히려 분리를 악화시켰다.

원인은 임계값이 아니라 표현이다. **한국어 재료명은 문자열이 아니라 합성명사다.**

    참기름 = [참] + 기름        들기름 = [들] + 기름
    애호박 = [애] + 호박        단호박 = [단] + 호박

핵심어는 항상 뒤, 수식어는 앞이다. 문자 유사도는 이 구조를 못 본다.
`참기름`↔`들기름` 은 3글자 중 2글자가 같아 "비슷"하지만 **요리에서는 서로 다른 재료**다.

## 구조로 바꾸면 결정적으로 판정된다

| 조건 | 판정 |
|---|---|
| 핵심어 불일치 | 즉시 탈락 — 유사도를 볼 필요가 없다 |
| 핵심어 일치 + 수식어 일치 | `exact` |
| 핵심어 일치 + 수식어가 whitelist | `rule` (국산·냉동·다진…) |
| 핵심어 일치 + 수식어 다름 | 🔴 **서로 다른 재료** — 검수 큐로 |
| 핵심어 일치 + 한쪽에만 수식어 | **하위어** — 애호박 ⊂ 호박. 별도 처리 |

퍼지 매칭은 오탈자(`얘호박`)에만 남기고, 임계값은 캘리브레이션으로 정한다
(`features.recommend.evaluation.threshold`).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Decomposed:
    raw: str
    modifiers: tuple[str, ...]
    head: str

    @property
    def is_bare(self) -> bool:
        return not self.modifiers


class HeadIndex:
    """사전의 재료명들로 핵심어 목록을 만들고, 임의 표기를 분해한다.

    핵심어 후보는 **사전에 단독으로 존재하는 이름**이다.
    `호박` 이 사전에 있으므로 `애호박` 은 [애] + 호박 으로 쪼개진다.
    `기름` 처럼 단독으로 없는 핵심어는 `extra_heads` 로 보충한다.
    """

    #: 🔴 1글자 핵심어는 쓰지 않는다. `간장` 이 [간]+`장` 으로 쪼개져
    #:    `진간장`([진]+간장) 과 핵심어가 달라지고, 구조 판정이 무의미해진다.
    MIN_HEAD_LEN = 2

    #: 사람이 검수한 1글자 예외. 실제로 생산적인 것만.
    CURATED_SHORT_HEADS = ("파",)

    #: 수식어를 떼고 남는 최소 음절. `생강` → `강` 같은 파괴를 막는다.
    #: (seeds/modifier_whitelist.yaml 의 `생` 항목 경고와 같은 규칙)
    MIN_REMAINDER = 2

    @staticmethod
    def derive_heads(names: list[str], min_count: int = 3,
                     min_len: int = MIN_HEAD_LEN) -> set[str]:
        """시드에서 핵심어를 **자동 유도한다.**

        손으로 적은 목록은 반드시 빠뜨린다. `min_count` 개 이상의 재료명이
        공유하는 접미사는 그 자체로 핵심어다 — `기름`(참·들·포도씨…),
        `버섯`(표고·느타리·새송이…), `김치`(배추·총각·깍두기…).

        시드가 커지면 자동으로 좋아진다는 것이 손으로 적는 것보다 나은 점이다.
        """
        from collections import Counter
        c: Counter[str] = Counter()
        for n in names:
            for k in range(min_len, len(n)):        # 진부분 접미사만
                c[n[-k:]] += 1
        return {suf for suf, cnt in c.items() if cnt >= min_count}

    def __init__(self, names: list[str], extra_heads: tuple[str, ...] | None = None,
                 derive: bool = True):
        self.names = set(names)
        self.heads: set[str] = set(names)
        if derive:
            self.heads |= self.derive_heads(names)
        self.heads |= set(extra_heads or self.CURATED_SHORT_HEADS)
        # 긴 핵심어를 먼저 시도한다 — '치즈' 보다 '크림치즈' 가 우선
        self._ordered = sorted(self.heads, key=len, reverse=True)

    def split(self, name: str) -> Decomposed:
        """가장 긴 접미 핵심어를 찾아 앞쪽을 수식어로 본다.

        수식어가 다시 사전 표제어면 더 쪼개지 않는다 — `배추김치` 의 `배추` 는
        수식어이지 별도 핵심어가 아니다.
        """
        name = name.strip()
        # 🔴 `name == h` 로 먼저 끊으면 안 된다. `참기름` 은 사전 표제어이므로
        #    자기 자신과 먼저 일치해 [참]+기름 으로 쪼개지지 않고, 그러면
        #    `들기름` 과의 관계가 'unrelated' 가 되어 구조 판정이 무의미해진다.
        #    **항상 가장 긴 진부분 접미사를 먼저 찾는다.**
        for h in self._ordered:
            if len(h) < len(name) and name.endswith(h):
                return Decomposed(name, (name[: -len(h)],), h)
        return Decomposed(name, (), name)      # 못 쪼개면 통째로 핵심어

    def relation(self, a: str, b: str, whitelist: set[str]) -> str:
        """두 표기의 관계. P3 캐스케이드의 판정값이 된다.

        Returns:
            'same'      — 같은 재료
            'rule'      — 수식어가 whitelist 라 같은 재료로 본다 (자동 확정 가능)
            'hyponym'   — 한쪽이 다른 쪽의 하위어 (애호박 ⊂ 호박). 검수 필요
            'sibling'   — 🔴 핵심어는 같으나 수식어가 달라 **다른 재료**. 검수 필요
            'unrelated' — 핵심어가 다르다

        🔴 **자동 확정은 `same` 과 `rule` 뿐이다.** 나머지는 전부 검수 큐로 간다.
        """
        a, b = a.strip(), b.strip()
        if a == b:
            return "same"

        # 🔴 둘 다 사전 표제어면 정의상 서로 다른 재료다.
        #    `건포도`↔`포도` 는 접미 관계이고 `건` 이 whitelist 에 있지만
        #    둘 다 등록된 재료이므로 절대 합치면 안 된다. 사전이 최종 권위다.
        both_registered = a in self.names and b in self.names

        # ① 직접 접미 관계 — 한쪽이 다른 쪽 뒤에 그대로 들어 있다
        long_, short_ = (a, b) if len(a) > len(b) else (b, a)
        if long_.endswith(short_):
            mod = long_[: -len(short_)]
            if (mod in whitelist and not both_registered
                    and len(short_) >= self.MIN_REMAINDER):
                return "rule"
            return "hyponym"

        # ② 핵심어 분해 비교
        da, db = self.split(a), self.split(b)
        if da.head != db.head:
            return "unrelated"
        diff = set(da.modifiers) ^ set(db.modifiers)
        if diff <= whitelist and not both_registered:
            return "rule"
        return "sibling"
