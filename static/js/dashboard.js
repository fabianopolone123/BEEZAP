/* Dashboard — clique numa métrica abre a lista que está por trás do número.
 *
 * Todo número do painel é acionável: os quatro cards, cada ponto do gráfico de dias,
 * cada fatia do donut e cada item da legenda. O conteúdo vem de um endpoint só
 * (`dashboard/detalhe/`), que aplica as MESMAS consultas dos cards e o alcance de
 * visualização de quem está logado — a janela mostra nome de cliente e trecho de
 * mensagem, então não pode ignorar a regra que a tela Conversas respeita.
 *
 * Uma requisição por abertura, sem poll: a janela é uma foto do momento do clique.
 */
(function () {
  'use strict';

  var script = document.querySelector('script[data-detail-url]');
  var modal = document.querySelector('[data-dash-modal]');
  if (!script || !modal) return;

  var DETALHE_URL = script.dataset.detailUrl;
  var CONVERSAS_URL = script.dataset.conversationsUrl;

  var dialog = modal.querySelector('.dash-modal-dialog');
  var tituloEl = modal.querySelector('[data-dash-title]');
  var subEl = modal.querySelector('[data-dash-sub]');
  var bodyEl = modal.querySelector('[data-dash-body]');
  var footEl = modal.querySelector('[data-dash-foot]');
  var countEl = modal.querySelector('[data-dash-count]');
  var hiddenEl = modal.querySelector('[data-dash-hidden]');

  var pedidoAtual = 0;      // descarta resposta de clique antigo (clique rápido)
  var ultimoFoco = null;    // para devolver o foco ao fechar

  /* ---------------- utilidades ---------------- */

  function esc(texto) {
    return String(texto == null ? '' : texto)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function plural(n, singular, pluralForma) {
    return n === 1 ? singular : pluralForma;
  }

  /* ---------------- abrir / fechar ---------------- */

  function abrir(titulo) {
    ultimoFoco = document.activeElement;
    tituloEl.textContent = titulo || 'Detalhe';
    subEl.textContent = '';
    footEl.hidden = true;
    bodyEl.innerHTML =
      '<div class="dash-skeleton"><span></span><span></span><span></span><span></span></div>';
    modal.hidden = false;
    // Sem rolagem atrás da janela (evita a página "pular" no fundo).
    document.body.style.overflow = 'hidden';
    if (dialog) dialog.focus();
  }

  function fechar() {
    modal.hidden = true;
    document.body.style.overflow = '';
    pedidoAtual += 1;  // qualquer resposta em voo passa a ser ignorada
    if (ultimoFoco && ultimoFoco.focus) ultimoFoco.focus();
  }

  modal.querySelectorAll('[data-dash-close]').forEach(function (btn) {
    btn.addEventListener('click', fechar);
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !modal.hidden) fechar();
  });

  /* ---------------- desenho das linhas ---------------- */

  function linhaConversa(item) {
    var quem = item.ultima_de
      ? '<b>' + esc(item.ultima_de) + ':</b> '
      : '';
    var mensagem = item.ultima_mensagem
      ? quem + esc(item.ultima_mensagem)
      : '<span class="dash-value is-empty">Sem mensagens</span>';
    var chip = item.status === 'closed' ? 'is-closed'
      : (item.status === 'pending' ? 'is-pending' : 'is-open');
    return '' +
      '<button type="button" class="dash-row" data-conversa="' + item.id + '">' +
        '<span class="dash-avatar' + (item.is_group ? ' is-group' : '') + '">' +
          esc(item.iniciais) + '</span>' +
        '<span class="dash-cell">' +
          '<span class="dash-name">' + esc(item.cliente) +
            (item.is_group ? '<span class="dash-group-badge">grupo</span>' : '') +
          '</span>' +
          '<span class="dash-msg">' + mensagem + '</span>' +
        '</span>' +
        '<span class="dash-cell is-secondary">' +
          '<span class="dash-label">Setor</span>' +
          '<span class="dash-value' + (item.setor ? '' : ' is-empty') + '">' +
            esc(item.setor || 'sem setor') + '</span>' +
        '</span>' +
        '<span class="dash-cell is-secondary">' +
          '<span class="dash-label">Atendendo</span>' +
          '<span class="dash-value' + (item.atendente ? '' : ' is-empty') + '">' +
            esc(item.atendente || 'ninguém ainda') + '</span>' +
        '</span>' +
        '<span class="dash-meta">' +
          '<span class="dash-time">' + esc(item.quando) + '</span>' +
          (item.nao_lidas
            ? '<span class="dash-unread">' + item.nao_lidas + '</span>'
            : '<span class="dash-chip ' + chip + '">' + esc(item.status_label) + '</span>') +
        '</span>' +
      '</button>';
  }

  function linhaTempo(item) {
    // Faixas iguais às do CSS: 5 min e 30 min.
    var classe = item.segundos <= 300 ? '' :
      (item.segundos <= 1800 ? ' is-medio' : ' is-alto');
    return '' +
      '<button type="button" class="dash-row is-time" data-conversa="' + item.id + '">' +
        '<span class="dash-avatar' + (item.is_group ? ' is-group' : '') + '">' +
          esc(item.iniciais) + '</span>' +
        '<span class="dash-cell">' +
          '<span class="dash-name">' + esc(item.cliente) +
            (item.is_group ? '<span class="dash-group-badge">grupo</span>' : '') +
          '</span>' +
          '<span class="dash-msg">' +
            esc(item.setor || 'sem setor') +
            (item.atendente ? ' · ' + esc(item.atendente) : '') +
          '</span>' +
        '</span>' +
        '<span class="dash-fluxo">' +
          'Cliente <strong>' + esc(item.cliente_em) + '</strong>' +
          ' → resposta <strong>' + esc(item.resposta_em) + '</strong>' +
        '</span>' +
        '<span class="dash-meta">' +
          '<span class="dash-tempo' + classe + '">' + esc(item.tempo) + '</span>' +
        '</span>' +
      '</button>';
  }

  function vazio(metrica) {
    var textos = {
      'ativas': ['Nenhuma conversa ativa', 'Tudo finalizado por aqui.'],
      'novas': ['Nenhuma conversa nova', 'Não chegou conversa nova nos últimos 7 dias.'],
      'finalizadas': ['Nenhum atendimento finalizado', 'Ainda não há atendimento encerrado.'],
      'tempo-medio': ['Sem tempo de resposta para medir',
                      'Precisa de uma conversa em que o cliente falou e alguém respondeu.'],
      'setor': ['Nenhum atendimento neste setor', 'Nada foi roteado para cá ainda.'],
      'dia': ['Nenhum atendimento neste dia', 'Nenhuma conversa teve atividade na data.']
    };
    var t = textos[metrica] || ['Nada para mostrar', ''];
    return '<div class="dash-empty"><span class="dash-empty-icon" aria-hidden="true">🗒️</span>' +
      '<strong>' + esc(t[0]) + '</strong><span>' + esc(t[1]) + '</span></div>';
  }

  function erro() {
    return '<div class="dash-empty"><span class="dash-empty-icon" aria-hidden="true">⚠️</span>' +
      '<strong>Não foi possível carregar</strong>' +
      '<span>Tente abrir de novo em alguns segundos.</span></div>';
  }

  /* ---------------- busca e render ---------------- */

  function carregar(params, tituloProvisorio) {
    abrir(tituloProvisorio);
    var meuPedido = ++pedidoAtual;
    var url = DETALHE_URL + '?' + new URLSearchParams(params).toString();

    fetch(url, {headers: {'X-Requested-With': 'XMLHttpRequest'}})
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (dados) {
        if (meuPedido !== pedidoAtual) return;   // clique mais novo já assumiu
        if (!dados || !dados.ok) { bodyEl.innerHTML = erro(); return; }

        tituloEl.textContent = dados.titulo;
        subEl.textContent = dados.subtitulo || '';

        var itens = dados.itens || [];
        if (!itens.length) {
          bodyEl.innerHTML = vazio(dados.metrica);
          footEl.hidden = true;
          return;
        }
        var desenha = dados.tipo === 'tempos' ? linhaTempo : linhaConversa;
        bodyEl.innerHTML = itens.map(desenha).join('');
        bodyEl.scrollTop = 0;

        var mostrando = itens.length;
        countEl.textContent = mostrando < dados.total
          ? 'Mostrando ' + mostrando + ' de ' + dados.total
          : mostrando + ' ' + plural(mostrando, 'registro', 'registros');
        if (dados.ocultas > 0) {
          hiddenEl.textContent = dados.ocultas + ' ' +
            plural(dados.ocultas, 'fora do seu alcance', 'fora do seu alcance');
          hiddenEl.hidden = false;
        } else {
          hiddenEl.hidden = true;
        }
        footEl.hidden = false;
      })
      .catch(function () {
        if (meuPedido === pedidoAtual) bodyEl.innerHTML = erro();
      });
  }

  /* Clique numa linha abre a conversa na tela Conversas, já naquele chat. */
  bodyEl.addEventListener('click', function (event) {
    var linha = event.target.closest('[data-conversa]');
    if (!linha) return;
    window.location.href = CONVERSAS_URL + '?conversa=' + linha.dataset.conversa;
  });

  /* ---------------- gatilhos ---------------- */

  document.querySelectorAll('[data-metrica]').forEach(function (card) {
    card.addEventListener('click', function () {
      var rotulo = card.querySelector('p');
      carregar({metrica: card.dataset.metrica}, rotulo ? rotulo.textContent : 'Detalhe');
    });
  });

  document.querySelectorAll('[data-dia]').forEach(function (ponto) {
    ponto.addEventListener('click', function () {
      carregar({metrica: 'dia', data: ponto.dataset.dia}, 'Atendimentos do dia');
    });
  });

  document.querySelectorAll('[data-setor]').forEach(function (item) {
    item.addEventListener('click', function () {
      var nome = item.querySelector('.legend-name');
      carregar({metrica: 'setor', setor: item.dataset.setor},
               nome ? nome.textContent : 'Setor');
    });
  });

  /* Clique no DONUT: um conic-gradient não tem elemento por fatia, então
     descobrimos o setor pelo ÂNGULO do clique em relação ao centro. As faixas
     (início/fim em %) vêm do servidor, as mesmas que pintaram o gradiente. */
  var donut = document.querySelector('[data-donut]');
  var dadosSetores = document.getElementById('dados-setores');
  if (donut && dadosSetores) {
    var setores = [];
    try { setores = JSON.parse(dadosSetores.textContent) || []; } catch (e) { setores = []; }

    donut.addEventListener('click', function (event) {
      if (!setores.length) return;
      var caixa = donut.getBoundingClientRect();
      var dx = event.clientX - (caixa.left + caixa.width / 2);
      var dy = event.clientY - (caixa.top + caixa.height / 2);

      // O buraco do donut no meio não seleciona nada (o clique ali é "no vazio").
      var raio = Math.sqrt(dx * dx + dy * dy);
      if (raio < caixa.width * 0.22) return;

      // conic-gradient começa às 12h e cresce no sentido do relógio; atan2 começa
      // às 3h. O +90° gira a origem, e o módulo 360 normaliza o negativo.
      var graus = (Math.atan2(dy, dx) * 180 / Math.PI + 90 + 360) % 360;
      var pct = graus / 360 * 100;

      for (var i = 0; i < setores.length; i++) {
        var s = setores[i];
        if (pct >= s.inicio && pct < s.fim) {
          carregar({metrica: 'setor', setor: s.id}, s.name);
          return;
        }
      }
      // Arredondamento pode deixar a última fatia terminando em 99.99%.
      var ultimo = setores[setores.length - 1];
      if (ultimo) carregar({metrica: 'setor', setor: ultimo.id}, ultimo.name);
    });
  }
}());
