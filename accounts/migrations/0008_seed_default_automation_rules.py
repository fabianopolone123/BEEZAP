from django.db import migrations


DEFAULT_RULES = [
    {
        'title': 'Atendimento padrao da empresa',
        'keywords': 'ola, oi, bom dia, boa tarde, boa noite, atendimento, ajuda, empresa',
        'customer_example': 'Ola, preciso de ajuda.',
        'response_text': 'Ola! Sou atendente da empresa e vou te ajudar. Me diga, por favor, o que voce precisa.',
        'internal_instruction': 'Responder como atendente da empresa. Nao dizer que e IA. Se faltar informacao, encaminhar para atendente.',
    },
    {
        'title': 'Encaminhar para atendente',
        'keywords': 'atendente, humano, falar com alguem, nao sei, duvida, resolver',
        'customer_example': 'Quero falar com um atendente.',
        'response_text': 'Vou encaminhar sua solicitacao para um atendente.',
        'internal_instruction': 'Usar quando a regra cadastrada nao tiver informacao suficiente para responder com seguranca.',
    },
    {
        'title': 'Horario de atendimento',
        'keywords': 'horario, funcionamento, aberto, atendimento, expediente',
        'customer_example': 'Qual o horario de atendimento?',
        'response_text': 'Vou verificar o horario de atendimento e, se necessario, encaminhar sua solicitacao para um atendente.',
        'internal_instruction': 'Nao inventar horario. Ajustar esta regra quando a empresa informar o horario correto.',
    },
    {
        'title': 'Financeiro e pagamentos',
        'keywords': 'boleto, pagamento, pix, segunda via, financeiro, vencimento',
        'customer_example': 'Preciso da segunda via do boleto.',
        'response_text': 'Para assuntos de pagamento, boleto ou segunda via, vou encaminhar sua solicitacao para o financeiro.',
        'internal_instruction': 'Nao informar dados bancarios, valores ou links se nao estiverem cadastrados em regra especifica.',
    },
    {
        'title': 'Vendas e planos',
        'keywords': 'preco, valor, plano, planos, contratar, orcamento, venda',
        'customer_example': 'Quais sao os planos?',
        'response_text': 'Posso te ajudar com informacoes comerciais. Para passar valores ou condicoes corretas, vou encaminhar sua solicitacao para um atendente.',
        'internal_instruction': 'Nao inventar precos, promocoes ou condicoes comerciais.',
    },
    {
        'title': 'Suporte e problemas',
        'keywords': 'erro, problema, suporte, nao funciona, acesso, dificuldade',
        'customer_example': 'Estou com problema para acessar.',
        'response_text': 'Entendi. Para te ajudar melhor, vou encaminhar sua solicitacao para o suporte.',
        'internal_instruction': 'Nao solicitar senhas, tokens ou dados sensiveis.',
    },
]


def seed_default_rules(apps, schema_editor):
    AutomationRule = apps.get_model('accounts', 'AutomationRule')

    for rule_data in DEFAULT_RULES:
        AutomationRule.objects.update_or_create(
            title=rule_data['title'],
            defaults={
                **rule_data,
                'sector': None,
                'is_active': True,
            },
        )


def remove_default_rules(apps, schema_editor):
    AutomationRule = apps.get_model('accounts', 'AutomationRule')
    AutomationRule.objects.filter(
        title__in=[rule_data['title'] for rule_data in DEFAULT_RULES]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0007_automationrule'),
    ]

    operations = [
        migrations.RunPython(seed_default_rules, remove_default_rules),
    ]
