"""Conversao de texto Markdown -> formatacao nativa do WhatsApp.

O atendente costuma colar texto estruturado (titulos, negrito, listas) que vem em
Markdown (`**negrito**`, `# titulo`, `* item`, `> citacao`). O WhatsApp NAO entende
Markdown: usa `*negrito*` (um asterisco), `_italico_`, `~tachado~`, nao tem titulo e
renderiza listas/citacoes por quebra de linha. Sem esta conversao os simbolos `**`/`#`
chegariam literais e o texto perderia a estrutura. Aplicado no envio de texto da
conversa (`conversation_send_view`).
"""
import re

# Marcadores temporarios (chars improvaveis no texto) para nao confundir negrito (**)
# com italico (*) durante a traducao — negrito vira placeholder antes do italico.
_BOLD = '\x01'
_ITALIC = '\x02'


def _inline(text):
    """Converte enfase inline (negrito/italico/link) de Markdown para WhatsApp.

    Ordem importa: negrito (`**`/`__`) primeiro, senao o italico de asterisco simples
    consumiria os asteriscos do negrito."""
    # Link [texto](url) -> texto (url)
    text = re.sub(r'\[([^\]]+)\]\(([^)\s]+)\)', r'\1 (\2)', text)
    # Negrito: **x** / __x__
    text = re.sub(r'\*\*(.+?)\*\*', _BOLD + r'\1' + _BOLD, text)
    text = re.sub(r'__(.+?)__', _BOLD + r'\1' + _BOLD, text)
    # Italico: *x* (asterisco simples restante) e _x_ (fora de palavra, ex.: nome_arquivo)
    text = re.sub(r'\*(.+?)\*', _ITALIC + r'\1' + _ITALIC, text)
    text = re.sub(r'(?<!\w)_(.+?)_(?!\w)', _ITALIC + r'\1' + _ITALIC, text)
    # Traduz para os marcadores do WhatsApp
    return text.replace(_BOLD, '*').replace(_ITALIC, '_')


def markdown_to_whatsapp(text):
    """Recebe o texto (com quebras de linha) e devolve pronto para o WhatsApp."""
    if not text:
        return text
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    out = []
    for line in text.split('\n'):
        stripped = line.lstrip()
        indent = line[:len(line) - len(stripped)]

        # Titulo (#, ##, ###...) -> linha inteira em negrito (WhatsApp nao tem titulo).
        m = re.match(r'#{1,6}\s+(.*)$', stripped)
        if m:
            # Remove enfase interna (a linha ja fica toda em negrito) para nao aninhar *.
            content = _inline(m.group(1)).replace('*', '').replace('_', '').strip()
            content = content.rstrip('#').strip()
            out.append('*' + content + '*' if content else '')
            continue

        # Item de lista (-, *, +) -> bullet visivel em qualquer versao do WhatsApp.
        m = re.match(r'[-*+]\s+(.*)$', stripped)
        if m:
            out.append(indent + '• ' + _inline(m.group(1)))
            continue

        # Lista numerada: mantem "1." / "1)" e converte o resto.
        m = re.match(r'(\d+[.)])\s+(.*)$', stripped)
        if m:
            out.append(indent + m.group(1) + ' ' + _inline(m.group(2)))
            continue

        # Citacao: WhatsApp suporta "> "; mantem e converte o conteudo.
        m = re.match(r'>\s?(.*)$', stripped)
        if m:
            out.append('> ' + _inline(m.group(1)))
            continue

        out.append(_inline(line))
    return '\n'.join(out)
