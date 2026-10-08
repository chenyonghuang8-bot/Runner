class MockCoach:
    """Explicit, deterministic placeholder. Never presented as real inference."""
    def reply(self, message: str, workout_count: int):
        return {
            'provider': 'mock', 'is_mock': True,
            'reply': f'已收到你的描述。当前档案中有 {workout_count} 次训练记录。这里暂时是模拟教练，还没有调用 DeepSeek；你的正式计划不会被修改。你可以先保存训练记录和身体反馈，后续接入教练分析。',
            'plan_changed': False,
        }
