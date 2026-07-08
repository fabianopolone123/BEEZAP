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


INTENT_CLASSIFICATION_PROMPT = """
Voce classifica a intencao de um cliente no atendimento do BEEzap.
Sua unica tarefa e escolher para qual SETOR a mensagem do cliente deve ir.
Voce recebe a lista de setores possiveis (nome e descricao).
Responda APENAS com o NOME EXATO de um setor da lista, sem mais nada.
Se a mensagem nao permitir decidir com seguranca, responda exatamente: INDEFINIDO
Nao explique, nao cumprimente, nao escreva frases. Responda so o nome do setor ou INDEFINIDO.
""".strip()


def build_intent_classification_messages(message, sectors_block):
    """Monta as mensagens para o modelo CLASSIFICAR a intencao em um setor.

    `sectors_block` e um texto com os setores disponiveis (nome + descricao).
    A saida esperada e apenas o nome de um setor ou 'INDEFINIDO'.
    """
    user_message = (message or '').strip()
    safe_sectors = (sectors_block or '').strip()

    return [
        {'role': 'system', 'content': INTENT_CLASSIFICATION_PROMPT},
        {
            'role': 'user',
            'content': (
                f'Setores disponiveis:\n{safe_sectors}\n\n'
                f'Mensagem do cliente:\n{user_message}\n\n'
                f'Responda so o nome do setor ou INDEFINIDO.'
            ),
        },
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
