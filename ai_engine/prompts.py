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


# Regra de controle anexada ao final do system prompt no MODO GENERATIVO (a IA
# escreve a resposta ao cliente). Garante um marcador legivel pelo codigo para
# decidir o roteamento, que o cliente nunca ve.
_GENERATIVE_FORMAT_RULE = (
    'FORMATO OBRIGATORIO DA SUA RESPOSTA:\n'
    '1) Primeiro, escreva SOMENTE a mensagem que o cliente vai ler (curta, educada, humana).\n'
    '2) Depois, em uma ultima linha separada, escreva o marcador de controle no formato '
    'exato: [SETOR: X]\n'
    'Onde X e o NOME EXATO de um setor da lista de setores disponiveis (quando voce ja '
    'sabe para onde encaminhar), ou GERAL (assunto indefinido / encaminhar ao setor geral), '
    'ou CONTINUAR (quando voce ainda precisa de mais informacao e fez uma pergunta ao cliente).\n'
    'O cliente NAO ve o marcador. Nunca escreva o marcador no meio da mensagem.'
)


def build_generative_reply_messages(instructions, sectors_block, transcript, history=''):
    """Monta as mensagens para o modelo GERAR a resposta ao cliente e decidir o
    roteamento (marcador [SETOR: ...]) no modo generativo.

    `instructions` sao as diretrizes/persona do painel; `sectors_block` a lista de
    setores; `transcript` a conversa atual (Cliente/Voce); `history` (opcional) um
    resumo de conversas anteriores com o mesmo contato.
    """
    base = (instructions or '').strip() or INTENT_CLASSIFICATION_PROMPT
    safe_sectors = (sectors_block or '').strip()
    system_prompt = (
        f'{base}\n\nSetores disponiveis:\n{safe_sectors}\n\n{_GENERATIVE_FORMAT_RULE}'
    )

    parts = []
    safe_history = (history or '').strip()
    if safe_history:
        parts.append(
            'Historico de conversas anteriores com este contato (contexto, pode '
            f'estar desatualizado):\n{safe_history}'
        )
    parts.append(f'Conversa atual:\n{(transcript or "").strip()}')
    parts.append('Escreva a proxima mensagem ao cliente e, na ultima linha, o marcador [SETOR: ...].')

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
