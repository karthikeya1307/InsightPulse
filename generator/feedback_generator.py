"""Dependent transaction records: reviews, survey responses, tickets and messages.

Every record is generated from *controlled generation metadata*: a target
sentiment, a set of themes and a language variant are chosen first; the text
is then composed from clauses that carry exactly those (theme, polarity) tags.
The ground-truth label is therefore derived by rule (see ``derive_sentiment``)
rather than assigned independently.
"""
from __future__ import annotations

import random
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from itertools import accumulate

from app.core.taxonomy import THEME_BY_CODE
from app.utils import id_generator as ids
from app.utils.text_preprocessing import text_hash
from generator import text_templates as T
from generator.config import (
    ID_YEAR, NPS_RANGE, RATING_DIST, REVIEW_CHANNEL_WEIGHTS, SCENARIO_EVENTS, SENTIMENT_DIST,
    SUPPORT_CHANNEL_WEIGHTS, THEME_DIST, ScaleConfig, ScenarioEvent,
)
from generator.master_generator import IST, rng_for

THEME_CODES = list(THEME_BY_CODE)


# ---------------------------------------------------------------------------
# Label derivation rule (documented in docs/business_rules.md)
# ---------------------------------------------------------------------------
def derive_sentiment(polarities: list[str]) -> str:
    """Sentiment label from clause polarities.

    * any 'pos' and any 'neg'      -> MIXED
    * only 'pos' (+ optional 'neu') -> POSITIVE
    * only 'neg' (+ optional 'neu') -> NEGATIVE   (sarcasm counts as 'neg')
    * only 'neu' or no clauses      -> NEUTRAL
    """
    has_pos, has_neg = "pos" in polarities, "neg" in polarities
    if has_pos and has_neg:
        return "MIXED"
    if has_pos:
        return "POSITIVE"
    if has_neg:
        return "NEGATIVE"
    return "NEUTRAL"


@dataclass
class Composition:
    text: str
    sentiment: str
    themes: list[str]  # theme codes
    is_sarcasm: bool = False
    is_ambiguous: bool = False
    style: str = "standard"
    clause_tags: list[tuple[str, str]] = field(default_factory=list)


def _decap(clause: str) -> str:
    """Lower-case the first letter for mid-sentence use, keeping 'I' and acronyms (UPI, OTP)."""
    first = clause.split(" ", 1)[0]
    if first == "I" or (len(first) > 1 and first.isupper()):
        return clause
    return clause[0].lower() + clause[1:]


OVERALL_RATE = 0.3  # share of single-polarity English texts with a theme-free overall opinion


class TextComposer:
    """Assembles feedback text from tagged clauses in English, Hindi or Telugu."""

    STYLES = ["standard", "casual", "formal", "terse", "emphatic", "emoji", "typo"]
    STYLE_WEIGHTS = [0.42, 0.15, 0.10, 0.12, 0.07, 0.08, 0.06]
    EMOJI = {"POSITIVE": ["😊", "👍", "❤️"], "NEGATIVE": ["😡", "👎", "😤"], "NEUTRAL": ["😐", "🙂"],
             "MIXED": ["🤔", "😕"]}

    def __init__(self, cfg: ScaleConfig, rng: random.Random):
        self.cfg, self.rng = cfg, rng

    # -- slot filling -----------------------------------------------------
    def _fill(self, template: str, product: dict) -> str:
        r = self.rng
        return template.format(
            product=product["product_name"],
            feature=r.choice(T.CATEGORY_FEATURES[product["category"]]),
            days=r.randint(2, 15), weeks=r.randint(2, 8),
            amount=r.choice([49, 99, 150, 199, 250, 300, 499, 650, 999, 1200, 1500]),
            order=f"ORD-{r.randint(10000, 99999)}",
        )

    def _polarities(self, sentiment: str, n: int) -> list[str]:
        if sentiment == "POSITIVE":
            return ["pos"] * n
        if sentiment == "NEGATIVE":
            return ["neg"] * n
        if sentiment == "NEUTRAL":
            return ["neu"] * n
        # MIXED: at least one pos and one neg
        pols = ["pos", "neg"] + [self.rng.choice(["pos", "neg"]) for _ in range(n - 2)]
        self.rng.shuffle(pols)
        return pols

    def compose(self, sentiment: str, themes: list[str], variant: str, product: dict,
                allow_ambiguous: bool = True) -> Composition:
        r = self.rng
        if sentiment == "MIXED" and len(themes) < 2:
            themes = themes + [r.choice([t for t in THEME_CODES if t not in themes])]
        if sentiment == "NEUTRAL" and allow_ambiguous and r.random() < self.cfg.ambiguous_rate:
            text = r.choice(T.AMBIGUOUS_EN if variant == "en" else T.AMBIGUOUS_ML[variant])
            return Composition(self._finish(text, variant), "NEUTRAL", [], is_ambiguous=True,
                               clause_tags=[])
        pols = self._polarities(sentiment, len(themes))
        if variant == "en":
            return self._compose_en(sentiment, themes, pols, product)
        return self._compose_ml(sentiment, themes, pols, variant)

    def _compose_en(self, sentiment: str, themes: list[str], pols: list[str], product: dict) -> Composition:
        r = self.rng
        clauses, sarcasm = [], False
        for i, (theme, pol) in enumerate(zip(themes, pols)):
            bank = T.EN_CLAUSES[theme]
            if pol == "neg" and i == 0 and r.random() < self.cfg.sarcasm_rate:
                tmpl, sarcasm = r.choice(bank["sarcasm"]), True
            elif pol == "neu" and r.random() < self.cfg.suggestion_rate:
                tmpl = r.choice(bank["suggestion"])
            else:
                tmpl = r.choice(bank[pol])
            clauses.append(self._fill(tmpl, product))
        text = clauses[0]
        for prev, pol, clause in zip(pols, pols[1:], clauses[1:]):
            conn = r.choice(T.CONNECTORS_EN["contrast" if prev != pol else "same"])
            text += conn + (clause if conn == ". " else _decap(clause))
        if sentiment in ("POSITIVE", "NEGATIVE") and not sarcasm and r.random() < OVERALL_RATE:
            overall = self._fill(r.choice(T.OVERALL_EN[pols[0]]), product)
            text = f"{overall}. {text}" if r.random() < 0.5 else f"{text.rstrip('.')}. {overall}"
        style = r.choices(self.STYLES, weights=self.STYLE_WEIGHTS)[0]
        if sarcasm and style == "typo":
            style = "standard"
        opener = "" if style == "terse" else r.choice(T.OPENERS_EN)
        closer = "" if style == "terse" else r.choice(T.CLOSERS_EN[sentiment])
        if opener:
            text = opener + _decap(text)
        text = text.rstrip(".") + "."
        if closer:
            text += " " + closer
        text = self._apply_style(text, style, sentiment)
        return Composition(text, derive_sentiment(pols), list(themes), is_sarcasm=sarcasm, style=style,
                           clause_tags=list(zip(themes, pols)))

    def _compose_ml(self, sentiment: str, themes: list[str], pols: list[str], variant: str) -> Composition:
        r = self.rng
        frames = T.ML_FRAMES[variant]
        clauses = []
        for theme, pol in zip(themes, pols):
            if theme == "TECHNICAL_ISSUES":
                clauses.append(r.choice(T.TECH_ML[variant][pol]))
            else:
                clauses.append(r.choice(frames[pol]).format(noun=T.THEME_NOUNS[theme][variant]))
        text = clauses[0]
        for prev, pol, clause in zip(pols, pols[1:], clauses[1:]):
            conn = r.choice(frames["contrast" if prev != pol else "same"])
            text += f", {conn} {clause}"
        style = "emoji" if r.random() < 0.08 else "standard"
        text = self._apply_style(self._finish(text, variant), style, sentiment)
        return Composition(text, derive_sentiment(pols), list(themes), style=style,
                           clause_tags=list(zip(themes, pols)))

    @staticmethod
    def _finish(text: str, variant: str) -> str:
        end = "।" if variant == "hi" else "."
        text = text.rstrip(".। ")
        return text[0].upper() + text[1:] + end if variant in ("en", "hi_rom", "te_rom") else text + end

    def _apply_style(self, text: str, style: str, sentiment: str) -> str:
        r = self.rng
        if style == "casual":
            return text.lower().rstrip(".")
        if style == "formal":
            return f"Dear team, {text[0].lower()}{text[1:]} Regards."
        if style == "emphatic":
            return text.rstrip(".") + "!!"
        if style == "emoji":
            return f"{text} {r.choice(self.EMOJI[sentiment])}"
        if style == "typo":
            words = text.split(" ")
            idx = [i for i, w in enumerate(words) if len(w) > 5 and w.isalpha()]
            if idx:
                i = r.choice(idx)
                w = words[i]
                j = r.randint(1, len(w) - 3)
                words[i] = w[:j] + w[j + 1] + w[j] + w[j + 2:]
            return " ".join(words)
        return text


# ---------------------------------------------------------------------------
# Sampling helpers
# ---------------------------------------------------------------------------
class DateSampler:
    """Daily volume with mild growth trend and weekend uplift."""

    def __init__(self, cfg: ScaleConfig, rng: random.Random):
        self.rng = rng
        n = (cfg.end_date - cfg.start_date).days + 1
        self.days = [cfg.start_date + timedelta(days=i) for i in range(n)]
        weights = [(1 + 0.4 * i / n) * (1.15 if d.weekday() >= 5 else 1.0) for i, d in enumerate(self.days)]
        self.cum = list(accumulate(weights))

    def sample_day(self) -> date:
        x = self.rng.random() * self.cum[-1]
        return self.days[bisect_left(self.cum, x)]

    def sample_dt(self, day: date | None = None) -> datetime:
        day = day or self.sample_day()
        secs = self.rng.randint(8 * 3600, 23 * 3600 - 1)
        return datetime.combine(day, time(0), tzinfo=IST) + timedelta(seconds=secs)


def weighted(rng: random.Random, dist: dict) -> str:
    return rng.choices(list(dist), weights=list(dist.values()))[0]


class EntityPicker:
    """Picks parents that already exist on the record date (signup/launch ordering)."""

    def __init__(self, rng: random.Random, customers: list[dict], products: list[dict]):
        self.rng = rng
        self.customers = customers
        self.products = products
        self.product_weights = [1 / (1 + 0.12 * i) for i in range(len(products))]
        self._signup = [date.fromisoformat(c["signup_date"]) for c in customers]
        self._launch = [date.fromisoformat(p["launch_date"]) for p in products]
        self._disc = [date.fromisoformat(p["discontinued_date"]) if p["discontinued_date"] else None
                      for p in products]

    def customer(self, d: date) -> dict:
        for _ in range(50):
            i = self.rng.randrange(len(self.customers))
            if self._signup[i] <= d:
                return self.customers[i]
        eligible = [i for i, s in enumerate(self._signup) if s <= d]
        return self.customers[self.rng.choice(eligible)]

    def product_ok(self, i: int, d: date) -> bool:
        return self._launch[i] <= d and (self._disc[i] is None or d <= self._disc[i])

    def product(self, d: date) -> dict:
        for _ in range(50):
            i = self.rng.choices(range(len(self.products)), weights=self.product_weights)[0]
            if self.product_ok(i, d):
                return self.products[i]
        raise RuntimeError(f"No product available on {d}")


def active_event(rng: random.Random, d: date, product: dict, products: list[dict], source: str
                 ) -> ScenarioEvent | None:
    """Return the scenario event that fires for this record, if any."""
    for ev in SCENARIO_EVENTS:
        if not (ev.start <= d <= ev.end) or source not in ev.sources:
            continue
        if ev.product_index is not None:
            if ev.product_index > len(products) or product["product_id"] != ids.product_id(ev.product_index):
                continue
        if ev.category is not None and product["category"] != ev.category:
            continue
        if rng.random() < ev.intensity:
            return ev
    return None


def choose_themes(rng: random.Random, cfg: ScaleConfig, source: str, primary: str | None = None) -> list[str]:
    n = rng.choices([1, 2, 3], weights=cfg.theme_count_weights)[0]
    themes = [primary or weighted(rng, THEME_DIST[source])]
    while len(themes) < n:
        t = weighted(rng, THEME_DIST[source])
        if t not in themes:
            themes.append(t)
    return themes


def choose_variant(rng: random.Random, cfg: ScaleConfig, customer: dict) -> str:
    lang = customer["preferred_language"]
    if lang == "en" or rng.random() >= cfg.native_language_use_prob:
        return "en"
    return f"{lang}_rom" if rng.random() < cfg.romanised_share else lang


def variant_language(variant: str) -> str:
    return variant.split("_")[0]


def rating_for(rng: random.Random, sentiment: str) -> int:
    dist = RATING_DIST[sentiment]
    return int(rng.choices(list(dist), weights=list(dist.values()))[0])


# ---------------------------------------------------------------------------
# Record generators
# ---------------------------------------------------------------------------
def _meta(comp: Composition, variant: str, event: ScenarioEvent | None, explained: bool = False) -> dict:
    return {
        "true_sentiment": comp.sentiment,
        "true_theme_ids": [THEME_BY_CODE[t].theme_id for t in comp.themes],
        "is_sarcasm": comp.is_sarcasm, "is_ambiguous": comp.is_ambiguous, "style": comp.style,
        "language_variant": variant, "scenario_event": event.event_code if event else "",
        "explained_mismatch": explained,
    }


def _maybe_explained_mismatch(rng: random.Random, cfg: ScaleConfig, sentiment: str, rating: int
                              ) -> tuple[int, bool]:
    """Legitimate boundary case: star rating opposite to text (customer mis-click)."""
    if sentiment in ("POSITIVE", "NEGATIVE") and rng.random() < cfg.explained_mismatch_rate:
        return (1 if sentiment == "POSITIVE" else 5), True
    return rating, False


def generate_reviews(cfg: ScaleConfig, seed: int, customers: list[dict], products: list[dict],
                     channels: list[dict]) -> list[dict]:
    rng = rng_for(seed, "reviews")
    sampler, picker = DateSampler(cfg, rng), EntityPicker(rng, customers, products)
    composer = TextComposer(cfg, rng)
    chan_ids = {c["channel_code"]: c["channel_id"] for c in channels}
    dts = sorted(sampler.sample_dt() for _ in range(cfg.n_reviews))  # chronological ids
    seq, rows = ids.IdSequence("review", ID_YEAR), []
    seen: set[tuple[str, str, str]] = set()
    for dt in dts:
        d = dt.date()
        customer, product = picker.customer(d), picker.product(d)
        event = active_event(rng, d, product, products, "REVIEW")
        if event:
            sentiment, themes = "NEGATIVE", choose_themes(rng, cfg, "REVIEW", event.theme_code)
        else:
            sentiment, themes = weighted(rng, SENTIMENT_DIST["REVIEW"]), choose_themes(rng, cfg, "REVIEW")
        variant = choose_variant(rng, cfg, customer)
        comp = composer.compose(sentiment, themes, variant, product)
        for _ in range(20):  # baseline data has no accidental duplicates; those are injected deliberately
            key = (customer["customer_id"], product["product_id"], text_hash(comp.text))
            if key not in seen:
                break
            customer = picker.customer(d)
            variant = choose_variant(rng, cfg, customer)
            comp = composer.compose(sentiment, themes, variant, product)
        seen.add(key)
        rating, explained = _maybe_explained_mismatch(rng, cfg, comp.sentiment, rating_for(rng, comp.sentiment))
        rows.append({
            "review_id": seq.next(), "customer_id": customer["customer_id"], "product_id": product["product_id"],
            "channel_id": chan_ids[weighted(rng, REVIEW_CHANNEL_WEIGHTS)], "rating": rating,
            "review_title": rng.choice(T.REVIEW_TITLES[comp.sentiment]), "review_text": comp.text,
            "review_date": dt.isoformat(), "language": variant_language(variant),
            "verified_purchase": rng.random() < 0.85, "helpful_votes": int(rng.expovariate(0.3)),
            "created_at": dt.isoformat(), "_meta": _meta(comp, variant, event, explained),
        })
    return rows


def generate_survey_responses(cfg: ScaleConfig, seed: int, customers: list[dict], products: list[dict],
                              surveys: list[dict]) -> list[dict]:
    rng = rng_for(seed, "survey_responses")
    sampler, picker = DateSampler(cfg, rng), EntityPicker(rng, customers, products)
    composer = TextComposer(cfg, rng)
    child = ids.ChildIdSequence("response")
    windows = [(s, date.fromisoformat(s["start_date"]), min(date.fromisoformat(s["end_date"]), cfg.end_date))
               for s in surveys]
    capacity = 990  # leave id space (max 999 per survey) for later duplicates
    product_by_id = {p["product_id"]: p for p in products}
    dts = sorted(sampler.sample_dt() for _ in range(cfg.n_survey_responses))
    rows = []
    seen: set[tuple[str, str, str]] = set()
    for dt in dts:
        d = dt.date()
        open_surveys = [s for s, a, b in windows if a <= d <= b and child.count(s["survey_id"]) < capacity]
        if not open_surveys:
            raise RuntimeError(f"No open survey with capacity on {d}; increase n_surveys")
        survey = rng.choice(open_surveys)
        customer = picker.customer(d)
        if survey["product_id"]:
            product = product_by_id[survey["product_id"]]
        else:
            product = picker.product(d)
        event = active_event(rng, d, product, products, "SURVEY_RESPONSE")
        if event:
            sentiment, themes = "NEGATIVE", choose_themes(rng, cfg, "SURVEY_RESPONSE", event.theme_code)
        else:
            sentiment = weighted(rng, SENTIMENT_DIST["SURVEY_RESPONSE"])
            primary = "CUSTOMER_SUPPORT" if survey["survey_type"] == "POST_SUPPORT" and rng.random() < 0.6 else None
            themes = choose_themes(rng, cfg, "SURVEY_RESPONSE", primary)
        variant = choose_variant(rng, cfg, customer)
        comp = composer.compose(sentiment, themes, variant, product)
        for _ in range(20):  # no accidental duplicates in the baseline; duplicates are injected deliberately
            key = (survey["survey_id"], customer["customer_id"], text_hash(comp.text))
            if key not in seen:
                break
            customer = picker.customer(d)
            variant = choose_variant(rng, cfg, customer)
            comp = composer.compose(sentiment, themes, variant, product)
        seen.add(key)
        rating, explained = _maybe_explained_mismatch(rng, cfg, comp.sentiment, rating_for(rng, comp.sentiment))
        nps = ""
        if survey["survey_type"] == "NPS":
            lo, hi = NPS_RANGE[comp.sentiment]
            nps = rng.randint(lo, hi)
        rows.append({
            "response_id": child.next(survey["survey_id"]), "survey_id": survey["survey_id"],
            "customer_id": customer["customer_id"], "product_id": product["product_id"],
            "channel_id": survey["channel_id"], "response_date": dt.isoformat(), "rating": rating,
            "nps_score": nps, "response_text": comp.text, "language": variant_language(variant),
            "created_at": dt.isoformat(), "_meta": _meta(comp, variant, event, explained),
        })
    return rows


def generate_support(cfg: ScaleConfig, seed: int, customers: list[dict], products: list[dict],
                     channels: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Tickets, their messages (exact total = n_support_messages) and lifecycle events."""
    rng = rng_for(seed, "support")
    sampler, picker = DateSampler(cfg, rng), EntityPicker(rng, customers, products)
    composer = TextComposer(cfg, rng)
    chan_ids = {c["channel_code"]: c["channel_id"] for c in channels}
    end_dt = datetime.combine(cfg.end_date, time(23, 59, 59), tzinfo=IST)
    # Plan ticket sizes so total messages equals the configured volume exactly.
    sizes, total = [], 0
    while total < cfg.n_support_messages:
        k = rng.choices([1, 2, 3, 4], weights=[0.10, 0.35, 0.35, 0.20])[0]
        k = min(k, cfg.n_support_messages - total)
        sizes.append(k)
        total += k
    opened = sorted(sampler.sample_dt() for _ in sizes)
    tseq, mseq = ids.IdSequence("ticket", ID_YEAR), ids.ChildIdSequence("message")
    tickets, messages, events = [], [], []
    for size, t0 in zip(sizes, opened):
        d = t0.date()
        customer, product = picker.customer(d), picker.product(d)
        event = active_event(rng, d, product, products, "SUPPORT_MESSAGE")
        if event:
            sentiment, primary = "NEGATIVE", event.theme_code
        else:
            sentiment, primary = weighted(rng, SENTIMENT_DIST["SUPPORT_MESSAGE"]), weighted(rng, THEME_DIST["SUPPORT_MESSAGE"])
        themes = choose_themes(rng, cfg, "SUPPORT_MESSAGE", primary)
        variant = choose_variant(rng, cfg, customer)
        tid = tseq.next()
        order = f"ORD-{rng.randint(10000, 99999)}"
        # Message timeline: C1, A1, C2, A2 (truncated by size and by the data end date)
        times = [t0]
        for _ in range(size - 1):
            times.append(times[-1] + timedelta(minutes=rng.randint(30, 36 * 60)))
        if times[-1] > end_dt:  # compress the conversation into the data window (keeps exact volume)
            span = (times[-1] - t0).total_seconds()
            room = (end_dt - t0).total_seconds()
            times = [t0 + timedelta(seconds=int((t - t0).total_seconds() * room / span)) for t in times]
        conv: list[dict] = []
        c1 = composer.compose(sentiment, themes, variant, product, allow_ambiguous=False)
        c1_text = c1.text
        if variant == "en":
            c1_text = rng.choice(T.SUPPORT_OPENERS_EN).format(order=order) + c1.text
        conv.append({"sender": "CUSTOMER", "text": c1_text, "comp": c1})
        follow_pol = rng.choices(["pos", "neg", "neu"], weights={
            "NEGATIVE": [0.45, 0.35, 0.20], "NEUTRAL": [0.5, 0.0, 0.5], "MIXED": [0.5, 0.3, 0.2],
            "POSITIVE": [0.6, 0.0, 0.4]}[c1.sentiment])[0]
        for i in range(1, len(times)):
            if i % 2 == 1:  # agent
                conv.append({"sender": "AGENT", "text": rng.choice(T.AGENT_REPLIES).format(ticket=tid, order=order),
                             "comp": None})
            else:  # customer follow-up
                fsent = {"pos": "POSITIVE", "neg": "NEGATIVE", "neu": "NEUTRAL"}[follow_pol]
                if variant == "en":
                    txt, _ = rng.choice(T.SUPPORT_FOLLOWUP[follow_pol])
                    comp = Composition(txt + ".", fsent, ["CUSTOMER_SUPPORT"], clause_tags=[("CUSTOMER_SUPPORT", follow_pol)])
                else:
                    comp = composer.compose(fsent, ["CUSTOMER_SUPPORT"], variant, product, allow_ambiguous=False)
                conv.append({"sender": "CUSTOMER", "text": comp.text, "comp": comp})
        # Lifecycle events derived from the conversation
        tev = [(None, "OPEN", times[0], "system")]
        if len(times) >= 2:
            tev.append(("OPEN", "IN_PROGRESS", times[1], "agent"))
        last_customer = [c for c in conv if c["sender"] == "CUSTOMER"][-1]["comp"]
        resolved_at = closed_at = None
        if len(times) >= 2:
            resolve_p = 0.95 if last_customer.sentiment == "POSITIVE" else (0.45 if last_customer.sentiment == "NEGATIVE" else 0.75)
            if rng.random() < resolve_p:
                cand = times[-1] + timedelta(hours=rng.randint(1, 72))
                if cand <= end_dt:
                    resolved_at = cand
                    tev.append(("IN_PROGRESS", "RESOLVED", cand, "agent"))
                    close = cand + timedelta(days=3)
                    if close <= end_dt:
                        closed_at = close
                        tev.append(("RESOLVED", "CLOSED", close, "system"))
        status = tev[-1][1]
        cat_theme = THEME_BY_CODE[primary].theme_id
        prio = "URGENT" if event else ("HIGH" if primary in ("PAYMENT", "REFUNDS_RETURNS") and sentiment == "NEGATIVE"
                                       else ("LOW" if sentiment == "NEUTRAL" else "MEDIUM"))
        channel = chan_ids[weighted(rng, SUPPORT_CHANNEL_WEIGHTS)]
        tickets.append({
            "ticket_id": tid, "customer_id": customer["customer_id"], "product_id": product["product_id"],
            "channel_id": channel, "category_theme_id": cat_theme,
            "subject": rng.choice(T.SUPPORT_SUBJECTS[primary]), "priority": prio, "status": status,
            "opened_at": t0.isoformat(), "resolved_at": resolved_at.isoformat() if resolved_at else "",
            "closed_at": closed_at.isoformat() if closed_at else "", "message_count": len(conv),
            "created_at": t0.isoformat(), "updated_at": tev[-1][2].isoformat(),
        })
        for (frm, to, when, who) in tev:
            events.append({"entity_type": "SUPPORT_TICKET", "entity_id": tid, "from_state": frm or "",
                           "to_state": to, "changed_by": who, "reason": "", "changed_at": when.isoformat()})
        for msg, when in zip(conv, times):
            row = {"message_id": mseq.next(tid), "ticket_id": tid, "message_seq": mseq.count(tid),
                   "sender_type": msg["sender"], "message_text": msg["text"], "sent_at": when.isoformat(),
                   "language": variant_language(variant) if msg["sender"] == "CUSTOMER" else "en",
                   "created_at": when.isoformat()}
            if msg["comp"] is not None:
                row["_meta"] = _meta(msg["comp"], variant, event if msg is conv[0] else None)
            messages.append(row)
    return tickets, messages, events
