"""Broad experience cues plus semantic type guards; source quotes are checked separately."""
import re

LABELS = {"concept": "개념 이해", "connection": "관심과의 연결", "selection": "후보 선택",
          "formulation": "주제 구체화", "unspecified": "유형 판단 유보"}


def negative_experience(value):
    for clause in re.split(r"[.!?。\n]|지만\s*|그런데\s*|하지만\s*", value):
        if re.search(r"어렵지\s*않|어려운.{0,5}(?:아니|않)|모르.{0,12}(?:아니|않)|이해.{0,10}안.{0,8}(?:아니|않)|(?:빙빙|겉돌|제자리|헤매|헷갈|막막|혼란|혼동|막히|막혀).{0,14}(?:아니|않)", clause):
            continue
        if re.search(r"모르겠다면|모르겠으면|모르면|어렵다면|어려울\s*때|어려우면|헷갈리면|막막하면|이해.{0,6}안\s*되면|(?:다른\s*사용자|사람들).{0,20}(?:어렵|어려|모르|막막)", clause):
            continue
        if re.search(r"어려운\s*(?:개념|용어|주제).{0,15}(?:설명|추천)", clause):
            continue
        if re.search(r"(?:사용자|연구자|학습자|사람들).{0,35}(?:어려움|이해.{0,8}안).{0,30}(?:연구|분석|측정)", clause):
            continue
        if re.search(r"모르겠|잘\s*모르|모르(?:겠)?어|모릅니다|이해.{0,15}(?:안|못|어려)|막막|막혀|막히|헷갈|혼란|혼동|헤매|겉돌|빙빙|제자리|너무\s*추상적|감이.{0,8}안|갈피.{0,8}못|안\s*(?:와\s*닿|와닿|잡혀|잡히|들어와|들어오)|진전.{0,8}없|어려워|어렵게\s*느껴|어렵다|어려움.{0,8}(?:있|겪)", clause):
            return True
    return False


def personal_connection(value):
    return bool(re.search(r"내\s*(?:관심|연구|주제|문제|방향)|내가.{0,12}(?:관심|연구)|제\s*(?:관심|연구)|현재\s*(?:관심|연구\s*문제)|왜\s*추천|추천.{0,8}이유", value))


def technical_connection(value):
    return (not personal_connection(value) and bool(re.search(r"임베딩|벡터|모델|알고리즘|데이터|가중치|추론|원리|메커니즘|기술", value))
            and bool(re.search(r"연결|결합|주입|반영", value)))


def difficulty_kind(proposed, value):
    if technical_connection(value):
        return "concept"
    if proposed == "connection" and not personal_connection(value):
        return "unspecified"
    generic = re.search(r"빙빙|겉돌|제자리|뭘\s*하고\s*있는|어디로.{0,8}(?:가|향)|지금.{0,8}어디", value)
    typed = re.search(r"개념|용어|이해|원리|후보|비교|고르|선택|구체화|연구\s*(?:문제|질문)|범위|내\s*관심", value)
    if (generic and not typed) or (len(value.strip()) <= 12 and re.search(r"막막|헷갈|혼란|헤매", value) and not typed):
        return "unspecified"
    return proposed if proposed in LABELS else "unspecified"


def guess_kind(value):
    if personal_connection(value):
        proposed = "connection"
    elif re.search(r"구체화|연구\s*(?:문제|질문|주제)|범위|좁히", value):
        proposed = "formulation"
    elif re.search(r"후보|비교|고르|고를|골라|선택|차이", value):
        proposed = "selection"
    elif re.search(r"개념|이해|설명|용어|뜻|원리|작동", value):
        proposed = "concept"
    else:
        proposed = "unspecified"
    return difficulty_kind(proposed, value)


def pending_difficulties(state):
    active = {d["id"] for d in state.get("difficulties", []) if d.get("id")}
    return [h for h in state.get("difficulty_history", []) if h.get("lifecycle") == "unconfirmed"
            and h.get("status") == "expressed" and h.get("id") not in active]


def difficulty_view(state):
    current = state.get("difficulties") or []
    if current:
        return "expressed" if any(d.get("status") == "expressed" for d in current) else "possible"
    return "unconfirmed" if pending_difficulties(state) else "clear" if state.get("difficulty_status") == "clear" else "unknown"
