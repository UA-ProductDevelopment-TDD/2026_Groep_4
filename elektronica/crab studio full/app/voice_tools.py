"""Realtime voice configuration kept on the local server."""

PERSONALITY = """You are Bobby, a friendly and playful Bittle robot dog. Speak in short, natural English sentences. You may call robot_status before moving and robot_action when a movement suits the conversation. Never invent sensor readings. A sent command is not proof that the physical robot completed it. If developerMode is true, do not call robot_action. Respect stop, quiet, and no immediately. Use only Petoi skill codes supplied in the function description."""

TOOLS = [
    {
        'type': 'function',
        'name': 'robot_status',
        'description': 'Read whether Bittle is connected, busy, in test mode, or in developer mode.',
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    },
    {
        'type': 'function',
        'name': 'robot_action',
        'description': 'Run one Petoi skill code such as ksit, kup, khi, kbalance, kwkF, kwkL, kwkR, or kbk. Walking actions are time limited.',
        'parameters': {
            'type': 'object',
            'properties': {
                'code': {'type': 'string', 'description': 'An exact Petoi skill code from the Studio function library.'},
                'duration_ms': {'type': 'integer', 'minimum': 200, 'maximum': 3000},
            },
            'required': ['code', 'duration_ms'],
            'additionalProperties': False,
        },
    },
]
