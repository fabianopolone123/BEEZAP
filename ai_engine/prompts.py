BASE_ATTENDANCE_PROMPT = """
Voce e um assistente de atendimento do BEEzap.
Responda de forma curta, clara e educada.
Use portugues do Brasil.
Nao invente informacoes.
Se nao souber a resposta, diga que vai encaminhar para um atendente.
Nao peca dados sensiveis.
Nao informe valores, prazos ou politicas se essas informacoes nao estiverem no contexto fornecido.
Nao diga que e uma IA.
Nao use termos tecnicos.
Mantenha a resposta adequada para WhatsApp.
Responda em no maximo 3 frases.
""".strip()

RULES_ATTENDANCE_PROMPT = """
Voce e um assistente de atendimento do BEEzap.
Responda em portugues do Brasil.
Responda de forma curta, clara e educada.
Use apenas as informacoes das regras fornecidas.
Nao invente valores, horarios, prazos, links, politicas ou procedimentos.
Se as regras nao tiverem informacao suficiente, responda exatamente:
Vou encaminhar sua solicitacao para um atendente.
Nao diga que e uma IA.
Nao use termos tecnicos.
Nao use markdown complexo.
Mantenha a resposta adequada para WhatsApp.
Responda em no maximo 3 frases.
""".strip()


def build_messages(message, context=None):
    user_message = message.strip()
    safe_context = (context or '').strip()

    content_parts = []
    if safe_context:
        content_parts.append(f'Contexto seguro:\n{safe_context}')
    content_parts.append(f'Mensagem do cliente:\n{user_message}')

    return [
        {'role': 'system', 'content': BASE_ATTENDANCE_PROMPT},
        {'role': 'user', 'content': '\n\n'.join(content_parts)},
    ]


def build_messages_with_rules(message, rules_context):
    user_message = message.strip()
    safe_rules_context = (rules_context or '').strip()

    return [
        {'role': 'system', 'content': RULES_ATTENDANCE_PROMPT},
        {
            'role': 'user',
            'content': (
                f'Regras encontradas:\n{safe_rules_context}\n\n'
                f'Mensagem do cliente:\n{user_message}'
            ),
        },
    ]
