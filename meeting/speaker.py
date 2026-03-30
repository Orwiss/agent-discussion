# speaker selection은 영어로 작성 (소형 모델의 한국어가 불안정하므로)

SPEAKER_SELECTION_PROMPT = (
    "Read the conversation above. Pick the next speaker from {agentlist}. "
    "Return only the name."
)


def create_phase_speaker_selection(agents, user):
    """페이즈별 speaker selection callable을 생성"""

    agent_map = {a.name: a for a in agents}
    creative_agents = [a for a in agents if a.name != "Moderator"]
    moderator = agent_map.get("Moderator")

    # 연속 발언 카운터
    consecutive = {"last": None, "count": 0}

    def _get_speaker_counts(messages):
        """메인 에이전트별 발언 횟수"""
        counts = {a.name: 0 for a in creative_agents}
        for m in messages:
            name = m.get("name", "")
            if name in counts:
                counts[name] += 1
        return counts

    def _least_spoken_agent(messages):
        """가장 적게 발언한 creative agent 반환"""
        counts = _get_speaker_counts(messages)
        min_count = min(counts.values()) if counts else 0
        for a in creative_agents:
            if counts.get(a.name, 0) == min_count:
                return a
        return creative_agents[0]

    def _needs_moderator(messages):
        """Moderator가 정리할 시점인지"""
        non_mod = 0
        for m in reversed(messages):
            if m.get("name") == "Moderator":
                break
            if m.get("name") in {a.name for a in creative_agents}:
                non_mod += 1
        return non_mod >= 3

    def phase_speaker_selection(last_speaker, groupchat):
        messages = groupchat.messages

        # 유저가 마지막 발언자면 auto
        if last_speaker == user:
            return "auto"

        # 연속 발언 추적 — 2회 연속이면 다른 에이전트로
        if last_speaker == consecutive["last"]:
            consecutive["count"] += 1
        else:
            consecutive["last"] = last_speaker
            consecutive["count"] = 1

        if consecutive["count"] >= 2:
            # 다른 에이전트 중 가장 발언 적은 에이전트
            return _least_spoken_agent(messages)

        # 유저 개입 기회: ALWAYS 모드일 때만 (NEVER 모드면 건너뜀)
        if getattr(user, 'human_input_mode', 'NEVER') == 'ALWAYS':
            turns_since_user = 0
            for m in reversed(messages):
                if m.get("name") == user.name:
                    break
                turns_since_user += 1
            if turns_since_user >= 5:
                return user

        # 페이즈 확인
        current_phase = "generate"
        if hasattr(user, "context_variables") and user.context_variables:
            current_phase = user.context_variables.get("current_phase", "generate")

        # Moderator 주기 체크
        if _needs_moderator(messages) and moderator and last_speaker != moderator:
            return moderator

        # === 페이즈별 ===
        if current_phase == "generate":
            # 발산: 발언 적은 에이전트 강제 지명 (round-robin 효과)
            return _least_spoken_agent(messages)

        elif current_phase == "deepen":
            # 심화: LLM이 맥락에 맞는 에이전트 선택하되, 밸런스 유지
            counts = _get_speaker_counts(messages)
            max_count = max(counts.values()) if counts else 0
            min_count = min(counts.values()) if counts else 0
            # 격차가 3 이상이면 강제 밸런스
            if max_count - min_count >= 3:
                return _least_spoken_agent(messages)
            return "auto"

        elif current_phase == "converge":
            # 수렴: Moderator 중심이지만 다른 에이전트도 참여
            if last_speaker != moderator and moderator:
                return moderator
            return _least_spoken_agent(messages)

        return "auto"

    return phase_speaker_selection
