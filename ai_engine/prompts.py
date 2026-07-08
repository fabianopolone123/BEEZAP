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


# Diretrizes padrao quando o admin nao definiu instrucoes proprias no painel.
INTENT_CLASSIFICATION_PROMPT = """
Voce classifica a intencao de um cliente no atendimento do BEEzap.
Sua unica tarefa e escolher para qual SETOR a mensagem do cliente deve ir.
Voce recebe a lista de setores possiveis (nome e descricao).
Se a mensagem nao permitir decidir com seguranca, responda INDEFINIDO.
""".strip()

# Regra de formato SEMPRE anexada ao final do system prompt. Garante que a saida
# seja lida pelo codigo (so o nome do setor / INDEFINIDO), mesmo que o admin
# escreva instrucoes livres no painel.
_OUTPUT_FORMAT_RULE = (
    'FORMATO DA RESPOSTA (obrigatorio): responda com o NOME EXATO de um setor da '
    'lista de setores disponiveis, ou a palavra INDEFINIDO. Nao escreva mais nada — '
    'sem cumprimento, sem explicacao, sem frases.'
)


def build_intent_classification_messages(message, sectors_block, history='', instructions=''):
    """Monta as mensagens para o modelo CLASSIFICAR a intencao em um setor.

    `sectors_block` e um texto com os setores disponiveis (nome + descricao).
    `history` (opcional) e um resumo curto de conversas anteriores com o MESMO
    contato, para a IA se inteirar do contexto. `instructions` (opcional) sao as
    diretrizes/persona definidas pelo admin no painel; se vazias, usa o padrao.
    A saida esperada e sempre apenas o nome de um setor ou 'INDEFINIDO' (garantido
    pela regra de formato anexada ao system prompt).
    """
    user_message = (message or '').strip()
    safe_sectors = (sectors_block or '').strip()
    safe_history = (history or '').strip()
    base_instructions = (instructions or '').strip() or INTENT_CLASSIFICATION_PROMPT
    system_prompt = f'{base_instructions}\n\n{_OUTPUT_FORMAT_RULE}'

    parts = [f'Setores disponiveis:\n{safe_sectors}']
    if safe_history:
        parts.append(
            'Historico recente com este contato (apenas contexto, pode estar '
            f'desatualizado):\n{safe_history}'
        )
    parts.append(f'Mensagem atual do cliente:\n{user_message}')
    parts.append('Responda so o nome do setor ou INDEFINIDO.')

    return [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': '\n\n'.join(parts)},
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
