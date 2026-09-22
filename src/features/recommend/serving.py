"""실서빙의 흐름 조립 — 백엔드에서 사전을 받아 두고, 요청이 오면 ① → ② → ③ 을 잇습니다.

`service.py` 가 ②③ 과 취향을 조립하고, 여기는 그 앞뒤를 잇습니다 — 사전 동기화, 요청에 실려 온
냉장고 · 알레르기를 엔진의 문맥으로 바꾸기, 응답과 로그 만들기. `service.py` 가 500줄을 넘어
한 파일에 더 얹지 않고 나눴습니다(02 의 5.1).

**사전이 없으면 추천하지 않습니다.** 백엔드에 아직 닿지 못했으면 `CatalogNotReadyError` 를 올리고
라우터가 503 으로 답합니다. 목업으로 대신하지 않습니다 — 목업의 레시피 번호는 백엔드 DB 에 없는
번호라, 200 으로 나가면 백엔드는 존재하지 않는 레시피를 화면에 그리려 합니다. 503 이면 백엔드가
자기 인기순으로 대신합니다.

주의: 추천 로그는 메모리(최근 `LOG_CAPACITY` 건, 조회용)와 파일(`RecommendationSink`, 보존용)에
   둡니다. DB 적재(DB 전환 M-05)는 아직입니다 — `recommendation_log.user_id` 가 `app_user` 를
   참조하는데 사용자의 정본이 백엔드에 있어 그 표가 비어 있고, 지금 이으면 전건이 실패합니다.
주의: 취향 원본은 파일 저장소(`profile_store_dir`)에 둡니다. 컨테이너에서는 지속 볼륨이 필요합니다.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID, uuid4

from config import Settings
from features.recommend import service
from features.recommend.backend_client import BackendClient
from features.recommend.engine import allergy, catalog, rerank, retrieval
from features.recommend.engine import candidate as plans
from features.recommend.engine.context import UserContext, build_context
from features.recommend.engine.taste import FlavorVector
from features.recommend.enums import DEFAULT_WEIGHTS, Stage, UserMode
from features.recommend.policy import POLICY_ID, RankingPolicy
from features.recommend.profile_store import (
    PRESENTED_PATH,
    JsonProfileStore,
    load_presented_flavors,
)
from features.recommend.schema import (
    EventAck,
    EventBatchIn,
    EventIn,
    OnboardingIn,
    OnboardingOut,
    RecommendationLogOut,
    RecommendPantryItem,
    RecommendRequest,
    RecommendResponse,
    TasteOut,
)
from features.recommend.stage import RankedItem, StageInfo, StageTrace, TraceTotals
from utils.errors import AppError, ExternalServiceError

logger = logging.getLogger(__name__)

#: 메모리에 두는 추천 로그의 상한. 이벤트가 어느 칸에서 나왔는지 찾을 때와 로그 조회에 씁니다.
LOG_CAPACITY = 5000
#: 소비기한이 이 일수 안이면 "임박" 입니다. 오늘 끝나는 것(0)부터 셉니다. 지난 것은 세지 않습니다.
EXPIRING_WITHIN_DAYS = 3
SERVING_MODE = "real"


class CatalogNotReadyError(AppError):
    """백엔드에서 사전을 아직 한 번도 받지 못했습니다. 추천할 레시피가 없습니다."""


@dataclass(frozen=True)
class SyncState:
    """마지막 동기화가 어떻게 끝났는가. 헬스체크와 지표가 읽습니다."""

    ready: bool
    version: str | None
    synced_at: datetime | None
    recipes: int
    last_error: str | None


class RecommendationSink:
    """추천 한 건과 행동 이벤트 한 건을 파일에 한 줄씩 남깁니다(JSONL, 날짜별 파일).

    메모리의 로그는 재배포 때 사라지는데 노출 기록은 나중에 복원할 수 없습니다(DB 전환 점검표
    4절). DB 적재(M-05)가 열릴 때까지 평가가 읽을 원본입니다. 이벤트도 함께 남깁니다 — 취향
    저장소는 맛이 있는 이벤트만 정책 상한까지 들고 있어 노출과 반응을 잇는 원본이 못 됩니다.
    `request_id` 로 두 파일을 이으면 칸별 · 순위별 반응과 노출확률 보정을 나중에 계산할 수
    있습니다. 쓰기가 실패해도 응답은 나갑니다 — 실패는 세어서 지표로 내보냅니다
    (`reco_log_write_failed`).
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._lock = threading.Lock()

    def write(self, log: RecommendationLogOut, items: Sequence[RankedItem]) -> None:
        record = {
            "log": log.model_dump(mode="json"),
            "items": [item.model_dump(mode="json") for item in items],
        }
        self._append("recommendations", log.created_at, [record], str(log.request_id))

    def write_events(self, events: Sequence[EventIn], received_at: datetime) -> None:
        """배치의 이벤트를 한 줄씩. 받은 시각도 적어 `occurred_at` 이 없어도 순서가 남습니다."""
        records = [
            {**event.model_dump(mode="json"), "received_at": received_at.isoformat()}
            for event in events
        ]
        self._append("events", received_at, records, f"{len(events)} events")

    def _append(
        self, kind: str, day: datetime, records: Sequence[Mapping[str, object]], what: str
    ) -> None:
        lines = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
        target = self._root / f"{kind}-{day:%Y%m%d}.jsonl"
        try:
            with self._lock:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("a", encoding="utf-8") as handle:
                    handle.write(lines)
        except OSError:
            service.bump("reco_log_write_failed")
            logger.exception("%s log not written: %s", kind, what)


class LiveServing:
    """실서빙 한 벌. 앱이 뜰 때 하나 만들고 내려갈 때 멈춥니다."""

    def __init__(
        self,
        client: BackendClient,
        personas: service.PersonaService,
        policy: RankingPolicy,
        *,
        sync_interval_sec: float,
        retry_interval_sec: float,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sink: RecommendationSink | None = None,
    ) -> None:
        self._client = client
        self._personas = personas
        self._policy = policy
        self._sync_interval = sync_interval_sec
        self._retry_interval = retry_interval_sec
        self._clock = clock
        self._sink = sink
        self._seed = catalog.load_seed()
        self._catalog: catalog.Catalog | None = None
        self._last_error: str | None = None
        self._logs: OrderedDict[UUID, RecommendationLogOut] = OrderedDict()
        self._logs_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── 동기화 ────────────────────────────────────────────────────
    def start(self) -> None:
        """동기화 스레드를 띄웁니다. 첫 동기화를 기다리지 않습니다 — 앱은 바로 떠야 합니다."""
        self._thread = threading.Thread(target=self._run, name="catalog-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._client.close()

    def sync_once(self) -> catalog.Catalog:
        """백엔드에서 전량을 받아 사전을 갈아 끼웁니다. 실패하면 **앞의 사전을 그대로 둡니다.**

        반쯤 읽은 사전으로 서빙하는 것보다 어제의 사전으로 서빙하는 편이 낫습니다.
        """
        started = time.perf_counter()
        ingredients = self._client.fetch_ingredients()
        recipes = self._client.fetch_recipes()
        built = catalog.build_catalog(ingredients, recipes, self._seed, self._clock())
        if not built.recipes:
            raise CatalogNotReadyError("백엔드가 서빙할 레시피를 한 건도 주지 않았습니다")
        self._catalog = built
        self._last_error = None
        service.bump("catalog_synced")
        if built.unmapped_ingredients:
            # 시드에 닿지 못한 재료는 알레르기 군을 모릅니다. 하드컷의 구멍이라 세어 둡니다.
            service.bump("catalog_unmapped_ingredients", len(built.unmapped_ingredients))
            logger.warning("ingredients without a seed match: %s", built.unmapped_ingredients)
        logger.info(
            "catalog synced version=%s recipes=%d ingredients=%d in %.1fs",
            built.version,
            len(built.recipes),
            len(built.corpus.ingredient_names),
            time.perf_counter() - started,
        )
        return built

    def state(self) -> SyncState:
        current = self._catalog
        return SyncState(
            ready=current is not None,
            version=None if current is None else current.version,
            synced_at=None if current is None else current.synced_at,
            recipes=0 if current is None else len(current.recipes),
            last_error=self._last_error,
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(self._attempt())

    def _attempt(self) -> float:
        """동기화를 한 번 해 보고 다음까지 기다릴 초를 돌려줍니다. **예외를 올리지 않습니다.**"""
        try:
            self.sync_once()
        except (ExternalServiceError, CatalogNotReadyError) as error:
            # 비밀값이 섞이지 않는 문구입니다(경로와 상태코드뿐). 그대로 남깁니다.
            self._last_error = str(error)
            logger.warning("catalog sync failed: %s", error)
        except Exception as error:
            # 주의: 여기서 놓치면 이 스레드가 죽습니다. 그러면 사전이 다시는 갱신되지 않고(처음이면
            #    추천이 영영 503) 에러는 어디에도 남지 않습니다. 예상 못 한 것도 받아서 남기고
            #    다시 해 봅니다. 값이 섞일 수 있어 문구 대신 종류만 상태에 둡니다.
            self._last_error = type(error).__name__
            logger.exception("catalog sync crashed")
        else:
            return self._sync_interval
        service.bump("catalog_sync_failed")
        return self._retry_interval

    # ── 추천 ──────────────────────────────────────────────────────
    def recommend(self, request: RecommendRequest) -> RecommendResponse:
        current = self._catalog
        if current is None:
            raise CatalogNotReadyError("레시피 사전을 아직 받지 못했습니다")
        now = self._clock()
        started = time.perf_counter()
        resolution = allergy.resolve(
            request.allergies, current.corpus.ingredient_names, current.allergen_groups
        )
        if resolution.unknown_labels:
            service.bump("allergy_label_unknown", len(resolution.unknown_labels))
            logger.warning(
                "unknown allergy labels user_id=%s: %s",
                request.user_id,
                list(resolution.unknown_labels),
            )
        ctx = self._context(request, current, now)
        ratio = rerank.exploration_ratio(ctx, self._policy)
        # 요청의 `max_missing` 이 사다리의 첫 칸입니다. 후보가 모자라면 거기서부터 풉니다.
        # 점수와 재정렬의 정책은 그대로입니다 — 바뀌는 것은 ① 의 부족 허용뿐입니다.
        ladder = replace(
            self._policy,
            max_missing=request.max_missing,
            max_missing_relaxed=max(self._policy.max_missing_relaxed, request.max_missing),
        )
        found = retrieval.retrieve(current, ctx, resolution, ladder, request.top_k, ratio)
        retrieval_ms = int((time.perf_counter() - started) * 1000)

        seed = uuid4().int & 0x7FFFFFFF
        weights = {**DEFAULT_WEIGHTS, **(request.weight_override or {})}
        ranked = service.rank_candidates(
            found.candidates,
            current.recipes,
            ctx,
            current.corpus,
            self._policy,
            top_k=request.top_k,
            weights=weights if request.weight_override else None,
            rng_seed=seed,
            max_missing_final=found.max_missing,
            serving_mode=SERVING_MODE,
            batch_versions={"feature_version": current.version, "cluster_version": None},
        )
        total_ms = int((time.perf_counter() - started) * 1000)
        trace = StageTrace(
            stages=[
                StageInfo(
                    name=Stage.RETRIEVAL,
                    in_count=found.scanned,
                    out_count=len(found.candidates),
                    latency_ms=retrieval_ms,
                    strategy="pantry_coverage",
                    fallback=None if found.stage == plans.FALLBACK_NONE else found.stage,
                    filters=found.filters,
                    params={
                        "max_missing": found.max_missing,
                        "staple_added": len(current.staple_ids),
                        "pantry_received": len(request.pantry),
                        "allergy_labels": ", ".join(request.allergies),
                        "allergy_labels_unknown": ", ".join(resolution.unknown_labels),
                        "allergy_blocked_ingredients": len(resolution.blocked_ids),
                    },
                ),
                *ranked.stages,
            ],
            totals=TraceTotals(
                latency_ms=total_ms,
                user_mode=UserMode.COLD if ctx.persona is None else ctx.persona.mode,
                degraded=found.stage != plans.FALLBACK_NONE or len(ranked.items) < request.top_k,
            ),
        )
        request_id = uuid4()
        log = RecommendationLogOut(
            request_id=request_id,
            user_id=request.user_id,
            session_id=request.session_id,
            model_version=POLICY_ID,
            config_hash=self._policy.fingerprint(weights),
            pantry_snapshot=sorted(ctx.own_pantry_ids or ()),
            pantry_detail=[_pantry_detail(item) for item in request.pantry],
            allergy_snapshot=sorted(resolution.blocked_ids),
            stage_trace=trace,
            served=[item.recipe_id for item in ranked.items],
            total_latency_ms=total_ms,
            created_at=now,
        )
        self._remember(log)
        if self._sink is not None:
            self._sink.write(log, ranked.items)
        return RecommendResponse(
            request_id=request_id,
            user_id=request.user_id,
            model_version=POLICY_ID,
            weights=weights,
            items=ranked.items,
            trace=trace if request.include_trace else None,
            served_at=now,
        )

    def _context(
        self, request: RecommendRequest, current: catalog.Catalog, now: datetime
    ) -> UserContext:
        known = current.corpus.ingredient_names
        own = [item.ingredient_id for item in request.pantry if item.ingredient_id in known]
        if len(own) < len(request.pantry):
            # 사전에 없는 재료 번호입니다. 백엔드와 사전이 어긋났다는 뜻이라 세어 둡니다.
            service.bump("pantry_ingredient_unknown", len(request.pantry) - len(own))
        return build_context(
            user_id=request.user_id,
            persona=self._personas.persona_for(request.user_id, now),
            pantry_ids=sorted(set(own) | current.staple_ids),
            own_pantry_ids=sorted(set(own)),
            expiring_ids=expiring_ingredients(request.pantry, current.shelf_life_days, now.date()),
            max_cook_minutes=request.max_minutes,
        )

    # ── 온보딩 · 이벤트 · 로그 ────────────────────────────────────
    def save_onboarding(self, user_id: int, body: OnboardingIn) -> OnboardingOut:
        try:
            persona = self._personas.save_onboarding(
                user_id, body.picks, body.scales, self._clock(), body.preferred_cuisines
            )
        except OSError:
            # 저장소에 쓰지 못했습니다(볼륨 없음 · 권한). 500 으로 나가되 원인을 세어 둡니다.
            service.bump("persona_store_error")
            raise
        current = self._catalog
        blocked = 0
        if current is not None:
            names, groups = current.corpus.ingredient_names, current.allergen_groups
            blocked = len(allergy.resolve(body.allergy_groups, names, groups).blocked_ids)
        return OnboardingOut(
            user_id=user_id,
            taste=TasteOut.from_vector([0.0 if value is None else value for value in persona.vec]),
            n_blocked_ingredients=blocked,
            preferred_cuisines=list(body.preferred_cuisines),
            allergy_groups=list(body.allergy_groups),
            unmapped_allergens=list(body.unmapped_allergens),
        )

    def record_events(self, batch: EventBatchIn, ack: EventAck) -> EventAck:
        """취향에 반영합니다. 받았다는 답(`ack`)은 계약대로 그대로 돌려줍니다."""
        now = self._clock()
        if self._sink is not None:
            self._sink.write_events(batch.events, now)
        try:
            self._personas.record_events(batch.events, self._flavor_of, now)
        except OSError:
            service.bump("persona_store_error")
            raise
        return ack

    def read_log(self, request_id: UUID) -> RecommendationLogOut | None:
        with self._logs_lock:
            return self._logs.get(request_id)

    def _flavor_of(self, recipe_id: int) -> FlavorVector | None:
        current = self._catalog
        recipe = None if current is None else current.recipes.get(recipe_id)
        return None if recipe is None else recipe.flavor_vec

    def _remember(self, log: RecommendationLogOut) -> None:
        with self._logs_lock:
            self._logs[log.request_id] = log
            while len(self._logs) > LOG_CAPACITY:
                self._logs.popitem(last=False)


def expiring_ingredients(
    pantry: Sequence[RecommendPantryItem], shelf_life_days: Mapping[int, int], today: date
) -> list[int]:
    """소비기한이 임박한 재료. 직접 받은 날짜가 우선이고, 없으면 구매일에 재료별 일수를 더합니다.

    둘 다 없으면 모르는 것입니다. 임박하다고도 아니라고도 하지 않습니다.
    """
    soon: list[int] = []
    for item in pantry:
        expires = item.expires_at
        days = shelf_life_days.get(item.ingredient_id)
        if expires is None and item.purchased_at is not None and days is not None:
            expires = date.fromordinal(item.purchased_at.toordinal() + days)
        if expires is not None and 0 <= (expires - today).days <= EXPIRING_WITHIN_DAYS:
            soon.append(item.ingredient_id)
    return soon


def _pantry_detail(item: RecommendPantryItem) -> dict[str, object]:
    return {
        "ingredient_id": item.ingredient_id,
        "expires_at": None if item.expires_at is None else item.expires_at.isoformat(),
        "expires_at_source": "user" if item.expires_at is not None else "unknown",
    }


def build(settings: Settings) -> LiveServing | None:
    """설정에서 실서빙을 조립합니다. 백엔드 주소가 없으면 None — 그때는 목업이 답합니다."""
    if not settings.backend_base_url:
        return None
    policy = RankingPolicy()
    client = BackendClient(
        settings.backend_base_url,
        settings.internal_api_key,
        ingredients_path=settings.backend_ingredients_path,
        recipes_path=settings.backend_recipes_path,
        timeout_sec=settings.backend_timeout_sec,
        page_size=settings.backend_page_size,
        on_invalid=lambda kind, count: service.bump(f"backend_{kind}_invalid", count),
    )
    personas = service.PersonaService(
        store=JsonProfileStore(Path(settings.profile_store_dir)),
        presented=load_presented_flavors(PRESENTED_PATH),
        policy=policy,
    )
    return LiveServing(
        client,
        personas,
        policy,
        sync_interval_sec=settings.catalog_sync_interval_sec,
        retry_interval_sec=settings.catalog_retry_interval_sec,
        sink=RecommendationSink(Path(settings.reco_log_dir)),
    )
