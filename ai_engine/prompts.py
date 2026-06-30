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
