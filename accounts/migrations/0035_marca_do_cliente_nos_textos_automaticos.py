"""Textos automaticos param de citar o nome do SISTEMA para o cliente final.

O chatbot de menu e o atendente virtual falam DIRETO com o cliente final da empresa.
Os textos padrao traziam o nome do proprio sistema fixo ("Seja bem-vindo(a) a
BEEZAP"), o que estava errado por dois motivos ao mesmo tempo:

1. depois da troca de marca (BEEZap -> BEEonBOARD), o cliente final passou a ser
   recebido com o nome ANTIGO do produto;
2. mais fundo do que isso: **quem se apresenta ao cliente final e a EMPRESA**, nao a
   plataforma. O cliente da PPM tem que ser recebido pela PPM.

O codigo passou a usar o placeholder `{empresa}` (ver `chatbot/handler.py`). Esta
migration cuida do que JA ESTA GRAVADO no banco de cada cliente: onde o texto salvo
citar o nome do sistema, troca por `{empresa}`, que e resolvido na hora do envio com
o nome da empresa dona da conversa.

So mexe em texto que realmente cita o nome do sistema — quem escreveu a propria
saudacao fica intacto.
"""

from django.db import migrations


# Nomes do sistema que nunca deveriam aparecer numa fala para o cliente final.
NOMES_DO_SISTEMA = ('BEEonBOARD', 'BEEZAP', 'BEEZap', 'Beezap', 'beezap')


def _trocar_por_placeholder(texto):
    """Devolve (novo_texto, mudou). Troca o nome do sistema por `{empresa}`."""
    if not texto:
        return texto, False
    novo = texto
    for nome in NOMES_DO_SISTEMA:
        novo = novo.replace(nome, '{empresa}')
    return novo, novo != texto


def aplicar(apps, schema_editor):
    MenuBotConfiguration = apps.get_model('accounts', 'MenuBotConfiguration')
    OpenAiConfiguration = apps.get_model('accounts', 'OpenAiConfiguration')

    campos_do_chatbot = (
        'greeting', 'menu_intro', 'confirmation_message',
        'invalid_message', 'handoff_message',
    )
    for config in MenuBotConfiguration.objects.all():
        alterados = []
        for campo in campos_do_chatbot:
            novo, mudou = _trocar_por_placeholder(getattr(config, campo, ''))
            if mudou:
                setattr(config, campo, novo)
                alterados.append(campo)
        if alterados:
            config.save(update_fields=alterados)

    # O prompt da IA e UM SO da plataforma; o nome da empresa entra como dado
    # dinamico em `build_system_prompt`, entao aqui basta tirar o nome fixo.
    for config in OpenAiConfiguration.objects.all():
        novo, mudou = _trocar_por_placeholder(config.instructions or '')
        if mudou:
            config.instructions = novo
            config.save(update_fields=['instructions'])


def desfazer(apps, schema_editor):
    """Sem volta automatica: nao da para saber qual nome estava escrito antes, e
    voltar a citar o nome do sistema seria justamente o defeito. Nada a fazer."""


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0034_consumo_de_ia_por_empresa'),
    ]

    operations = [
        migrations.RunPython(aplicar, desfazer),
    ]
