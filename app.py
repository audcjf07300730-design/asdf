
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import List, Literal, Optional

import streamlit as st
from pydantic import BaseModel, Field
from openai import OpenAI

BASE = Path(__file__).resolve().parent
SCENARIOS = json.loads((BASE/"scenario_presets.json").read_text(encoding="utf-8"))

class UseScope(BaseModel):
    service_role: Literal[
        "production_supply","continuous_backup","emergency_makeup",
        "diagnostic_test","return_to_service","quality_decision","ANY"
    ]
    duty: Literal["normal_load","reduced_load","test_load","any"]
    duration: Literal["continuous","short_duration","any"]

class SemanticConstraint(BaseModel):
    target_asset: str
    effect: Literal["quantitative_limit","prohibit_use","allow_use","caution","release","none"]
    applies_to_use: UseScope
    exclusive_permission: bool = False
    limit_value: Optional[float] = None
    limit_unit: str = ""
    condition: str = ""
    release_condition: str = ""
    source_system: Literal["CMMS","MOC","Handover"]
    source_record_id: str = ""
    explicit_link: bool = True

class ExtractionResult(BaseModel):
    semantic_constraints: List[SemanticConstraint] = Field(default_factory=list)
    semantic_conflicts: List[str] = Field(default_factory=list)
    unresolved: List[str] = Field(default_factory=list)

st.set_page_config(page_title="Industrial Gas Decision Support PoC", page_icon="🧪", layout="wide")

st.markdown("""
<style>
.block-container {padding-top:1.1rem;padding-bottom:2rem;max-width:1500px;}
.hero {padding:1.1rem 1.35rem;border-radius:18px;background:linear-gradient(135deg,#0b3b67,#1261a0);color:white;margin-bottom:1rem;}
.hero h1 {margin:0;font-size:1.9rem;}
.hero p {margin:.35rem 0 0 0;opacity:.9;}
.kcard {padding:.9rem 1rem;border:1px solid #d9e2ec;border-radius:14px;background:#fff;margin-bottom:.55rem;}
.safe {background:#edf8ef;border-left:5px solid #2e7d32;}
.warn {background:#fff8e8;border-left:5px solid #f9a825;}
.danger {background:#fff0f0;border-left:5px solid #c62828;}
.info {background:#eef6ff;border-left:5px solid #1565c0;}
.purple {background:#f5f1ff;border-left:5px solid #6a5acd;}
.muted {color:#64748b;font-size:.9rem;}
.step {font-weight:700;color:#0b3b67;margin-bottom:.35rem;}
hr {margin:1rem 0;}
</style>
""", unsafe_allow_html=True)

def use_matches(scope, requested):
    return (
        scope.get("service_role") in ("ANY", requested.get("service_role"))
        and scope.get("duty") in ("any", requested.get("duty"))
        and scope.get("duration") in ("any", requested.get("duration"))
    )

def build_prompts(cmms, moc, handover, requested):
    system = """
너는 산업가스 플랜트 의사결정지원 시스템의 '자연어 운전제약 의미추출 모듈'이다.
최종 운전승인·설비조작·공급능력 산정을 하지 마라.
오직 CMMS, MOC, Handover 자연어에서 제약의 의미를 구조화한다.

반드시 지킬 것:
- target_asset은 입력의 정확한 설비명을 사용.
- 숫자 상한은 quantitative_limit.
- 특정 목적 금지는 prohibit_use, 허용은 allow_use.
- '만 허용'이면 exclusive_permission=true.
- 진단시험 허용을 정상 생산공급 허용으로 확대하지 않음.
- 비상보충 허용을 지속 백업공급 허용으로 확대하지 않음.
- 공식 해제/원상복귀가 명시된 경우만 release.
- 구두전달/확인필요는 공식 release로 확정하지 않음.
- 입력에 없는 정보를 만들지 않음.
"""
    user = f"""
설비별 requested_use:
{json.dumps(requested, ensure_ascii=False, indent=2)}

[CMMS]
{cmms}

[MOC]
{moc}

[Handover]
{handover}

위 자연어 운전제약을 구조화하라.
"""
    return system, user

def measurement_trust(analyzer, extraction):
    if analyzer["data_quality"] in ("불확실","불량"):
        return "UNRESOLVED"
    if analyzer["maintenance_status"] not in ("정상",""):
        return "UNRESOLVED"
    if analyzer["diagnostic_alarm"] not in ("없음",""):
        return "UNRESOLVED"
    for c in extraction.get("semantic_constraints",[]):
        if c.get("target_asset")=="Analyzer-01" and c.get("effect") in ("caution","prohibit_use"):
            return "CAUTION"
    return "TRUSTED"

def qualify(asset, cap, requested, hard, extraction):
    if hard.get("trip_active"):
        return dict(
            equipment_health="FAILED", work_control="BLOCKED",
            target_use_qualification="PROHIBITED",
            confirmed=0.0, conditional=0.0,
            reason="Trip 활성 — 구조화 Hard Constraint"
        )
    if hard.get("isolation_active"):
        return dict(
            equipment_health="NORMAL", work_control="BLOCKED",
            target_use_qualification="PROHIBITED",
            confirmed=0.0, conditional=0.0,
            reason="Isolation 활성 — 구조화 Hard Constraint"
        )

    rel=[c for c in extraction.get("semantic_constraints",[]) if c.get("target_asset")==asset]

    q=[c for c in rel if c.get("effect")=="quantitative_limit"
       and c.get("explicit_link") is True
       and use_matches(c.get("applies_to_use",{}),requested)]
    prohib=[c for c in rel if c.get("effect")=="prohibit_use"
            and c.get("explicit_link") is True
            and use_matches(c.get("applies_to_use",{}),requested)]
    allow=[c for c in rel if c.get("effect")=="allow_use"
           and c.get("explicit_link") is True
           and use_matches(c.get("applies_to_use",{}),requested)]
    exclusive_other=[c for c in rel if c.get("effect")=="allow_use"
                     and c.get("explicit_link") is True
                     and c.get("exclusive_permission") is True
                     and not use_matches(c.get("applies_to_use",{}),requested)]
    release=[c for c in rel if c.get("effect")=="release" and c.get("explicit_link") is True]

    if release:
        q=[]; prohib=[]

    # 현재 목적 금지가 있다면 다른 목적 허용보다 우선.
    if prohib:
        restorable=any(bool(c.get("release_condition")) for c in prohib)
        return dict(
            equipment_health="NORMAL", work_control="CLEAR",
            target_use_qualification="PROHIBITED",
            confirmed=0.0, conditional=cap if restorable else 0.0,
            reason="현재 requested_use에 대한 명시적 사용금지"
        )

    # 다른 목적만 exclusive 허용: 현재 목적에는 사용 금지.
    if exclusive_other and not allow:
        return dict(
            equipment_health="NORMAL", work_control="CLEAR",
            target_use_qualification="PROHIBITED",
            confirmed=0.0, conditional=0.0,
            reason="다른 운전목적만 독점적으로 허용"
        )

    if q:
        pct=min(float(c["limit_value"]) for c in q if c.get("limit_value") is not None)
        val=cap*pct/100
        return dict(
            equipment_health="DEGRADED", work_control="CLEAR",
            target_use_qualification="ALLOWED_WITH_LIMIT",
            confirmed=val, conditional=val,
            reason=f"승인된 정량 운전제한 {pct:g}% 적용"
        )

    if allow:
        return dict(
            equipment_health="NORMAL", work_control="CLEAR",
            target_use_qualification="ALLOWED",
            confirmed=cap, conditional=cap,
            reason="현재 requested_use에 대한 명시적 허용"
        )

    caution=[c for c in rel if c.get("effect")=="caution"
             and c.get("explicit_link") is True
             and use_matches(c.get("applies_to_use",{}),requested)]
    if caution:
        return dict(
            equipment_health="DEGRADED", work_control="CLEAR",
            target_use_qualification="UNRESOLVED",
            confirmed=0.0, conditional=cap,
            reason="추가 확인이 필요한 정성적 운전제약"
        )

    return dict(
        equipment_health="NORMAL", work_control="CLEAR",
        target_use_qualification="ALLOWED",
        confirmed=cap, conditional=cap,
        reason="사용을 막는 Hard Constraint 또는 목적별 제약 없음"
    )

def calc_report(demand, capacities, requested, hard, analyzer, extraction):
    contracts={}
    for asset in ["압축기 A","압축기 B","기화기 A","기화기 B"]:
        contracts[asset]=qualify(
            asset, capacities[asset], requested[asset], hard[asset], extraction
        )
    confirmed=sum(v["confirmed"] for v in contracts.values())
    conditional=sum(v["conditional"] for v in contracts.values())
    trust=measurement_trust(analyzer, extraction)
    return {
        "demand":demand,
        "confirmed":confirmed,
        "conditional":conditional,
        "measurement_trust":trust,
        "contracts":contracts,
        "llm_extraction":extraction
    }

# -------------------------
# Cloud secrets / public-demo protection
# -------------------------
def get_secret(name, default=""):
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name, default)

OPENAI_API_KEY=get_secret("OPENAI_API_KEY", "")
DEMO_ACCESS_CODE=get_secret("DEMO_ACCESS_CODE", "")
DEFAULT_MODEL=get_secret("OPENAI_MODEL", "gpt-5.6-luna")

if "live_calls" not in st.session_state:
    st.session_state["live_calls"]=0

def live_unlocked(code):
    # 공개 배포에서는 DEMO_ACCESS_CODE가 설정되어 있을 때만 Live LLM 허용
    return bool(DEMO_ACCESS_CODE) and code == DEMO_ACCESS_CODE

# -------------------------
# Sidebar / scenario
# -------------------------
with st.sidebar:
    st.header("시연 설정")
    scenario_id=st.selectbox(
        "대표 시나리오",
        list(SCENARIOS.keys()),
        format_func=lambda x:f"{x} · {SCENARIOS[x]['title']}"
    )
    s=SCENARIOS[scenario_id]
    st.markdown(f"**연구 포인트**  \n{s['research_point']}")

    st.divider()
    presentation_mode=st.toggle("발표용 간결 화면", value=True)

    st.divider()
    st.header("Live LLM")
    model_options=["gpt-5.6-luna","gpt-5.6-terra","gpt-5.6-sol"]
    default_index=model_options.index(DEFAULT_MODEL) if DEFAULT_MODEL in model_options else 0
    model=st.selectbox("모델",model_options,index=default_index)
    effort=st.selectbox("Reasoning effort",["none","low","medium"],index=1)
    access_code=st.text_input(
        "발표자 코드",
        type="password",
        help="공개 링크에서 API 비용 오남용을 막기 위한 코드입니다."
    )
    unlocked=live_unlocked(access_code)

    if not OPENAI_API_KEY:
        st.info("Cloud secret에 API Key가 없어 Offline 모드만 사용할 수 있습니다.")
    elif not DEMO_ACCESS_CODE:
        st.info("DEMO_ACCESS_CODE가 없어 공개 배포에서는 Live LLM을 잠가두었습니다.")
    elif unlocked:
        st.success("Live LLM 잠금 해제")
    else:
        st.caption("발표자 코드 입력 시 Live LLM 사용 가능")

    st.caption(f"이 세션 Live 호출: {st.session_state['live_calls']} / 6")

s=SCENARIOS[scenario_id]

st.markdown(
    f"""<div class="hero">
    <h1>산업가스 공급연속성 의사결정지원 PoC</h1>
    <p>{scenario_id} · {s['title']} — {s['story']}</p>
    </div>""",
    unsafe_allow_html=True
)

# -------------------------
# Editable settings
# -------------------------
top1,top2,top3=st.columns([1.1,1,1])
with top1:
    demand=st.number_input("고객 요구량",min_value=0.0,value=float(s["customer_demand"]),step=2.5,key=f"demand_{scenario_id}")
with top2:
    st.markdown("**Requested use**")
    st.caption("압축기 = 정상 생산공급 / 기화기 = 지속 백업공급")
with top3:
    st.markdown("**데이터 성격**")
    st.caption("연구용 합성데이터 · 실제 플랜트 내부자료 아님")

if not presentation_mode:
    st.subheader("구조화 Hard Evidence")
    cols=st.columns(4)
    hard={}
    for i,a in enumerate(["압축기 A","압축기 B","기화기 A","기화기 B"]):
        with cols[i]:
            st.markdown(f"**{a}**")
            hard[a]={
                "trip_active":st.checkbox("Trip 활성",value=s["hard"][a]["trip_active"],key=f"{scenario_id}_{a}_trip"),
                "isolation_active":st.checkbox("Isolation 활성",value=s["hard"][a]["isolation_active"],key=f"{scenario_id}_{a}_iso")
            }
else:
    hard=s["hard"]

if not presentation_mode:
    st.subheader("Analyzer current structured state")
    aa,ab,ac=st.columns(3)
    with aa:
        q=st.selectbox("Data quality",["양호","불확실","불량"],index=["양호","불확실","불량"].index(s["analyzer"]["data_quality"]),key=f"{scenario_id}_q")
    with ab:
        m=st.selectbox("Maintenance",["정상","작업 중","점검 필요"],index=["정상","작업 중","점검 필요"].index(s["analyzer"]["maintenance_status"]),key=f"{scenario_id}_m")
    with ac:
        alarm=st.selectbox("Diagnostic alarm",["없음","보조가스 압력 이상","장치 이상"],index=0,key=f"{scenario_id}_alarm")
    analyzer={"data_quality":q,"maintenance_status":m,"diagnostic_alarm":alarm}
else:
    analyzer=s["analyzer"]

caps=s["capacities"].copy()
requested=s["requested_uses"]

tabs=st.tabs(["1. 원천정보","2. LLM 의미추출","3. 상태계약","4. 공급결과","5. 발표용 요약"])

with tabs[0]:
    st.markdown('<div class="step">STEP 1 · 서로 다른 원천정보를 함께 본다</div>',unsafe_allow_html=True)
    c1,c2,c3=st.columns(3)
    with c1:
        st.markdown("#### CMMS")
        cmms=st.text_area("",value=s["texts"]["CMMS"],height=230,label_visibility="collapsed",key=f"{scenario_id}_cmms")
    with c2:
        st.markdown("#### MOC")
        moc=st.text_area("",value=s["texts"]["MOC"],height=230,label_visibility="collapsed",key=f"{scenario_id}_moc")
    with c3:
        st.markdown("#### Handover")
        hand=st.text_area("",value=s["texts"]["Handover"],height=230,label_visibility="collapsed",key=f"{scenario_id}_hand")

    st.markdown("#### Structured Hard Evidence")
    hard_rows=[]
    for a,v in hard.items():
        hard_rows.append({
            "설비":a,
            "Trip":"ACTIVE" if v["trip_active"] else "CLEAR",
            "Isolation":"ACTIVE" if v["isolation_active"] else "CLEAR",
            "정상용량":caps[a]
        })
    st.dataframe(hard_rows,use_container_width=True,hide_index=True)

with tabs[1]:
    st.markdown('<div class="step">STEP 2 · LLM은 자연어의 운전제약만 구조화한다</div>',unsafe_allow_html=True)
    x,y=st.columns(2)
    with x:
        run_live=st.button(
            "🤖 Live LLM 의미추출",
            type="primary",
            use_container_width=True,
            disabled=not (OPENAI_API_KEY and unlocked and st.session_state["live_calls"] < 6)
        )
    with y:
        run_offline=st.button("🧪 검증된 Offline 결과 사용",use_container_width=True)

    if run_live:
        sys,user=build_prompts(cmms,moc,hand,requested)
        try:
            client=OpenAI(api_key=OPENAI_API_KEY)
            with st.status("LLM이 운전제약을 구조화하는 중...",expanded=True) as status:
                st.write(f"모델: `{model}`")
                st.write("CMMS / MOC / Handover 분석")
                response=client.responses.parse(
                    model=model,
                    instructions=sys,
                    input=user,
                    reasoning={"effort":effort},
                    text_format=ExtractionResult
                )
                parsed=response.output_parsed
                if parsed is None:
                    raise RuntimeError("Structured output parsing failed.")
                st.session_state[f"ext_{scenario_id}"]=parsed.model_dump()
                st.session_state[f"mode_{scenario_id}"]=f"LIVE · {model}"
                st.session_state["live_calls"]+=1
                status.update(label="의미추출 완료",state="complete",expanded=False)
        except Exception as e:
            st.error("Live LLM 호출 중 오류가 발생했습니다.")
            st.exception(e)

    if run_offline:
        st.session_state[f"ext_{scenario_id}"]=s["offline_extraction"]
        st.session_state[f"mode_{scenario_id}"]="OFFLINE · 검증된 합성결과"

    extraction=st.session_state.get(f"ext_{scenario_id}",s["offline_extraction"])
    mode=st.session_state.get(f"mode_{scenario_id}","OFFLINE · 검증된 합성결과")
    st.success(f"현재 추출결과: {mode}")

    if not presentation_mode:
        st.json(extraction,expanded=2)
    else:
        rows=[]
        for c in extraction["semantic_constraints"]:
            rows.append({
                "설비":c["target_asset"],
                "의미":c["effect"],
                "적용 목적":c["applies_to_use"]["service_role"],
                "제한값":"" if c.get("limit_value") is None else f"{c['limit_value']:g}{c.get('limit_unit','')}",
                "해제/확인 조건":c.get("release_condition",""),
                "근거":c.get("source_record_id","")
            })
        st.dataframe(rows,use_container_width=True,hide_index=True)
        if extraction.get("unresolved"):
            st.warning("추가확인: "+" / ".join(extraction["unresolved"]))

with tabs[2]:
    st.markdown('<div class="step">STEP 3 · 같은 설비도 현재 운전목적에 맞춰 자격을 부여한다</div>',unsafe_allow_html=True)
    extraction=st.session_state.get(f"ext_{scenario_id}",s["offline_extraction"])
    report=calc_report(demand,caps,requested,hard,analyzer,extraction)
    for asset,row in report["contracts"].items():
        with st.container(border=True):
            c1,c2,c3,c4=st.columns([1.05,1,1.2,1.3])
            c1.markdown(f"### {asset}")
            c1.caption(requested[asset]["service_role"])
            c2.markdown("**Health**"); c2.code(row["equipment_health"])
            c3.markdown("**Work control**"); c3.code(row["work_control"])
            c4.markdown("**Target-use**"); c4.code(row["target_use_qualification"])
            st.caption(row["reason"])
            a,b=st.columns(2)
            a.metric("확정 기여량",f"{row['confirmed']:.1f}",border=True)
            b.metric("조건부 기여량",f"{row['conditional']:.1f}",border=True)

with tabs[3]:
    st.markdown('<div class="step">STEP 4 · 상태계약을 결정론적 공급계산에 연결한다</div>',unsafe_allow_html=True)
    extraction=st.session_state.get(f"ext_{scenario_id}",s["offline_extraction"])
    report=calc_report(demand,caps,requested,hard,analyzer,extraction)
    a,b,c,d=st.columns(4)
    a.metric("고객 요구량",f"{demand:.1f}",icon=":material/factory:",border=True)
    b.metric("확정 공급능력",f"{report['confirmed']:.1f}",delta=f"{report['confirmed']-demand:+.1f}",border=True)
    c.metric("조건부 공급능력",f"{report['conditional']:.1f}",delta=f"{report['conditional']-demand:+.1f}",border=True)
    d.metric("측정신뢰",report["measurement_trust"],icon=":material/science:",border=True)

    if report["confirmed"]>=demand:
        st.markdown('<div class="kcard safe"><b>현재 확정 공급능력만으로 고객수요 충족</b></div>',unsafe_allow_html=True)
    elif report["conditional"]>=demand:
        st.markdown(
            f'<div class="kcard warn"><b>현재 {demand-report["confirmed"]:.1f} 부족.</b> '
            f'추가 확인이 긍정적으로 해결되면 조건부 공급능력 {report["conditional"]:.1f}로 수요 충족 가능.</div>',
            unsafe_allow_html=True
        )
    else:
        st.markdown(
            f'<div class="kcard danger"><b>현재 및 조건부 공급능력 모두 부족.</b> '
            f'조건부 기준 {demand-report["conditional"]:.1f} 부족.</div>',
            unsafe_allow_html=True
        )

    rows=[]
    for asset,row in report["contracts"].items():
        rows.append({
            "설비":asset,"Requested use":requested[asset]["service_role"],
            "확정":row["confirmed"],"조건부":row["conditional"],"판정근거":row["reason"]
        })
    st.dataframe(rows,use_container_width=True,hide_index=True)

with tabs[4]:
    st.markdown('<div class="step">발표/심사용 한 화면 요약</div>',unsafe_allow_html=True)
    extraction=st.session_state.get(f"ext_{scenario_id}",s["offline_extraction"])
    report=calc_report(demand,caps,requested,hard,analyzer,extraction)

    left,right=st.columns([1.45,1])
    with left:
        st.markdown(f"### {scenario_id} · {s['title']}")
        st.write(s["story"])
        st.markdown(f"**연구 포인트:** {s['research_point']}")
        st.markdown("#### 핵심 판정")
        for asset,row in report["contracts"].items():
            st.write(
                f"- **{asset}** · {row['target_use_qualification']} · "
                f"확정 {row['confirmed']:.1f} / 조건부 {row['conditional']:.1f}"
            )
    with right:
        st.metric("수요",f"{demand:.1f}",border=True)
        st.metric("확정",f"{report['confirmed']:.1f}",delta=f"{report['confirmed']-demand:+.1f}",border=True)
        st.metric("조건부",f"{report['conditional']:.1f}",delta=f"{report['conditional']-demand:+.1f}",border=True)
        st.metric("Measurement trust",report["measurement_trust"],border=True)

    if extraction.get("unresolved"):
        st.markdown("#### 추가 확인")
        for u in extraction["unresolved"]:
            st.write(f"- {u}")

    st.markdown(
        '<div class="kcard info"><b>시스템의 역할</b><br>'
        'LLM은 자연어의 의미를 읽고, Hard Rule은 넘으면 안 되는 선을 강제하며, '
        '목적별 상태계약은 그 의미가 어떤 운전목적에 적용되는지 정리하고, '
        '결정론적 계산기는 최종 공급량을 계산한다.</div>',
        unsafe_allow_html=True
    )

    st.download_button(
        "📥 현재 시나리오 결과 JSON",
        data=json.dumps(report,ensure_ascii=False,indent=2),
        file_name=f"{scenario_id}_poc_result.json",
        mime="application/json",
        use_container_width=True
    )

st.divider()
st.caption("연구용 합성 데이터 기반 PoC · 실제 운전승인/설비조작을 자동 수행하지 않음 · AIRFIRST 내부자료를 사용하지 않음")
