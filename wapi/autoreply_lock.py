"""Trava de atendimento automatico que vale ENTRE PROCESSOS.

Problema real: `gpt/attendant.py` e `chatbot/handler.py` evitavam processar a mesma
conversa duas vezes com um `set()` na memoria do processo. Com `--workers 2` no
gunicorn, cada worker tem o SEU set — uma rajada de mensagens caindo em processos
diferentes passava pelas duas travas e **o cliente recebia o menu (ou a resposta da
IA) duas vezes**.

O outro lado do mesmo defeito: a mensagem rejeitada pela trava era simplesmente
descartada. Se o cliente digitasse "1" enquanto a thread anterior ainda rodava e nao
mandasse mais nada, a escolha nunca era processada e a conversa ficava parada, sem
cair em fila nenhuma.

A solucao usa o proprio banco, que e o unico estado compartilhado entre os workers:
`Conversation.auto_reply_lock_at` guarda "estou processando desde". Tomar a trava e um
UPDATE condicional — atomico por definicao, sem precisar de Redis nem de tabela nova:

    UPDATE conversation SET auto_reply_lock_at = agora
     WHERE id = X AND (auto_reply_lock_at IS NULL OR auto_reply_lock_at < agora - TTL)

Se o UPDATE afetou 1 linha, este processo tem a trava. Se afetou 0, outro esta com
ela. O TTL existe para um worker morto (o gunicorn mata worker no timeout) nao deixar
a conversa travada para sempre.

REPROCESSA A ULTIMA MENSAGEM: ao liberar a trava, quem estava rodando checa se chegou
mensagem nova durante o processamento e, se chegou, roda de novo — assim a escolha do
cliente nao se perde.
"""

from datetime import timedelta

from django.utils import timezone


# Tempo maximo que uma trava vale. Depois disso qualquer processo pode tomar,
# assumindo que quem a tinha morreu (worker reciclado, timeout do gunicorn).
LOCK_TTL = timedelta(minutes=2)


def acquire(conversation_id, now=None):
    """Tenta tomar a trava desta conversa. True = tomou, False = outro esta com ela."""
    from accounts.models import Conversation
    from django.db.models import Q

    now = now or timezone.now()
    expirada = now - LOCK_TTL
    afetadas = (
        Conversation.objects
        .filter(pk=conversation_id)
        .filter(Q(auto_reply_lock_at__isnull=True) | Q(auto_reply_lock_at__lt=expirada))
        .update(auto_reply_lock_at=now)
    )
    return bool(afetadas)


def release(conversation_id):
    """Libera a trava. Sempre chamado no `finally` de quem a tomou."""
    from accounts.models import Conversation
    Conversation.objects.filter(pk=conversation_id).update(auto_reply_lock_at=None)


def held_by_other(conversation_id, now=None):
    """A trava esta com outro processo AGORA? (usado so em diagnostico/teste)."""
    from accounts.models import Conversation
    now = now or timezone.now()
    return Conversation.objects.filter(
        pk=conversation_id, auto_reply_lock_at__gte=now - LOCK_TTL,
    ).exists()
