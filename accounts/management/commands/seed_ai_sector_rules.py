from django.core.management.base import BaseCommand

from accounts.models import AutomationRule, Sector


DEFAULT_RULES = [
    {
        'sector_names': ('Compras', 'Vendas'),
        'sector_description': (
            'Atende clientes interessados em compras, produtos, servicos, '
            'orcamentos, cotacoes, propostas comerciais e fornecimento.'
        ),
        'title': 'IA - Direcionamento para Compras',
        'keywords': (
            'comprar, compra, compras, orcamento, orçamento, cotacao, cotação, '
            'pedido, produto, produtos, preco, preço, valor, proposta, comercial, '
            'servico, serviço, fornecimento, fornecedor'
        ),
        'customer_example': 'Quero comprar um produto ou pedir um orcamento.',
        'response_text': 'Encaminhar para o setor de Compras.',
        'internal_instruction': 'Use esta regra quando o cliente demonstrar interesse em compra, orcamento, cotacao, pedido, produto, preco, proposta comercial ou fornecimento.',
    },
    {
        'sector_names': ('Financeiro',),
        'sector_description': (
            'Atende assuntos financeiros como boletos, pagamentos, faturas, '
            'notas fiscais, cobrancas, vencimentos, pix e recibos.'
        ),
        'title': 'IA - Direcionamento para Financeiro',
        'keywords': 'boleto, pagamento, pagar, fatura, nota fiscal, nf, financeiro, cobranca, cobrança, segunda via, vencimento, pix, recibo',
        'customer_example': 'Preciso de boleto, fatura, nota fiscal ou falar sobre pagamento.',
        'response_text': 'Encaminhar para o setor Financeiro.',
        'internal_instruction': 'Use esta regra quando o cliente falar sobre pagamento, boleto, fatura, nota fiscal, cobranca, pix ou segunda via.',
    },
]


class Command(BaseCommand):
    help = 'Cria/atualiza regras basicas para a IA direcionar conversas por setor.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='Atualiza tambem regras existentes com os textos padrao.',
        )

    def handle(self, *args, **options):
        overwrite = options['overwrite']
        created = 0
        updated = 0
        sectors_updated = 0
        skipped = []

        for rule_data in DEFAULT_RULES:
            sector = self._find_sector(rule_data['sector_names'])
            if sector is None:
                skipped.append('/'.join(rule_data['sector_names']))
                continue
            if not (sector.description or '').strip():
                sector.description = rule_data['sector_description']
                sector.save(update_fields=['description', 'updated_at'])
                sectors_updated += 1

            defaults = {
                'sector': sector,
                'keywords': AutomationRule.normalize_keywords(rule_data['keywords']),
                'customer_example': rule_data['customer_example'],
                'response_text': rule_data['response_text'],
                'internal_instruction': rule_data['internal_instruction'],
                'is_active': True,
            }
            rule, was_created = AutomationRule.objects.get_or_create(
                title=rule_data['title'],
                defaults=defaults,
            )
            if was_created:
                created += 1
                continue
            if overwrite:
                for field, value in defaults.items():
                    setattr(rule, field, value)
                rule.save(update_fields=[*defaults.keys(), 'updated_at'])
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f'Regras processadas. Criadas: {created}. Atualizadas: {updated}. '
            f'Setores descritos: {sectors_updated}.'
        ))
        if skipped:
            self.stdout.write(self.style.WARNING(
                'Setores nao encontrados, regras ignoradas: ' + ', '.join(skipped)
            ))

    def _find_sector(self, names):
        for name in names:
            sector = Sector.objects.filter(name__iexact=name).first()
            if sector is not None:
                return sector
        return None
