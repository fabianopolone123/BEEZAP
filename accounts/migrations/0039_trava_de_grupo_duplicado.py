"""UMA conversa por grupo, por empresa.

Duas mensagens de um grupo NOVO chegando quase juntas faziam dois webhooks criarem
duas conversas com o mesmo JID (o codigo consulta e depois cria, e a tabela nao tinha
unicidade), e o historico do grupo rachava entre as duas. Aconteceu em producao com
120363257947973768@g.us; as duplicatas existentes foram unificadas pelo
`merge_contact_conversations` ANTES desta migracao.

A trava cobre so `chat_type='group'` com `external_id` preenchido: e o caso chaveado
unicamente pelo JID, e linhas antigas sem `external_id` nao podem derrubar a migracao.
"""


from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0038_remove_campos_mortos'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='conversation',
            constraint=models.UniqueConstraint(condition=models.Q(('chat_type', 'group'), models.Q(('external_id', ''), _negated=True)), fields=('company', 'external_id'), name='unique_group_conversation_per_company'),
        ),
    ]
