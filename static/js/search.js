/* Tela Pesquisar — garimpa o histórico do atendimento.
 *
 * Não há botão "Pesquisar": digitar já busca (com 300 ms de espera, para não disparar
 * uma consulta por tecla). Cada requisição carrega um número de série e só a resposta
 * do pedido mais novo é desenhada — sem isso, uma resposta lenta de "not" chegaria
 * depois de "nota" e sobrescreveria o resultado certo.
 *
 * O termo aparece REALÇADO nos trechos: é o que transforma "achei 12 conversas" em
 * "achei, e é isto aqui".
 */
(function () {
  'use strict';

  var script = document.querySelector('script[data-results-url]');
  var page = document.querySelector('.search-page');
  if (!script || !page) return;

  var RESULTS_URL = script.dataset.resultsUrl;
  var CONVERSAS_URL = script.dataset.conversationsUrl;

  var campoQ = page.querySelector('[data-search-q]');
  var botaoLimpar = page.querySelector('[data-search-clear]');
  var botaoReset = page.querySelector('[data-search-reset]');
  var filtros = Array.prototype.slice.call(page.querySelectorAll('[data-search-filter]'));
  var resultados = page.querySelector('[data-search-results]');
  var resumo = page.querySelector('[data-search-summary]');

  var pedido = 0;
  var timer = null;

  function esc(t) {
    return String(t == null ? '' : t)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  /* Realça o termo no trecho. Escapa PRIMEIRO e só então injeta a marcação, senão o
     conteúdo da mensagem (que é texto de cliente) viraria HTML. */
  function realcar(texto, termo) {
    var seguro = esc(texto);
    if (!termo) return seguro;
    var alvo = esc(termo).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    try {
      return seguro.replace(new RegExp('(' + alvo + ')', 'gi'), '<mark>$1</mark>');
    } catch (e) {
      return seguro;
    }
  }

  function parametros() {
    var params = new URLSearchParams();
    var q = (campoQ.value || '').trim();
    if (q) params.set('q', q);
    filtros.forEach(function (campo) {
      var valor = (campo.value || '').trim();
      if (valor) params.set(campo.dataset.searchFilter, valor);
    });
    return params;
  }

  function temAlgumFiltro(params) {
    var vazio = true;
    params.forEach(function () { vazio = false; });
    return !vazio;
  }

  function linha(item, termo) {
    var trechos = (item.trechos || []).map(function (t) {
      return '' +
        '<span class="search-excerpt' + (t.direcao === 'in' ? '' : ' is-out') + '">' +
          '<span class="search-excerpt-meta">' + esc(t.quem) + ' · ' + esc(t.quando) + '</span>' +
          '<span class="search-excerpt-text">' + realcar(t.texto, termo) + '</span>' +
        '</span>';
    }).join('');

    var extras = item.total_trechos > (item.trechos || []).length
      ? '<span class="search-more">+ ' + (item.total_trechos - item.trechos.length) +
        ' outra(s) mensagem(ns) nesta conversa</span>'
      : '';

    var chip = item.status === 'closed' ? 'is-closed'
      : (item.status === 'pending' ? 'is-pending' : 'is-open');

    return '' +
      '<button type="button" class="search-row" data-conversa="' + item.id + '">' +
        '<span class="search-row-head">' +
          '<span class="search-avatar' + (item.is_group ? ' is-group' : '') + '">' +
            esc(item.iniciais) + '</span>' +
          '<span class="search-row-id">' +
            '<span class="search-name">' + realcar(item.cliente, termo) +
              (item.is_group ? '<span class="search-badge">grupo</span>' : '') +
            '</span>' +
            '<span class="search-meta">' +
              (item.setor ? esc(item.setor) : 'sem setor') +
              ' · ' + (item.atendente ? esc(item.atendente) : 'sem atendente') +
              (item.quando ? ' · ' + esc(item.quando) : '') +
            '</span>' +
          '</span>' +
          '<span class="search-chip ' + chip + '">' + esc(item.status_label) + '</span>' +
        '</span>' +
        (trechos
          ? '<span class="search-excerpts">' + trechos + extras + '</span>'
          : '<span class="search-last">' + esc(item.ultima) + '</span>') +
      '</button>';
  }

  function vazio(termo, comFiltro) {
    if (termo) {
      return '<div class="search-empty"><span aria-hidden="true">🔍</span>' +
        '<strong>Nada encontrado para "' + esc(termo) + '"</strong>' +
        '<span>Tente uma palavra mais curta, ou tire um filtro.</span></div>';
    }
    if (comFiltro) {
      return '<div class="search-empty"><span aria-hidden="true">🗂️</span>' +
        '<strong>Nenhuma conversa com esses filtros</strong>' +
        '<span>Experimente ampliar o período ou tirar um filtro.</span></div>';
    }
    return '';
  }

  function buscar() {
    var params = parametros();
    var termo = params.get('q') || '';
    botaoLimpar.hidden = !campoQ.value;

    if (!temAlgumFiltro(params)) {
      // Sem nada preenchido: volta para a dica, em vez de listar o histórico inteiro.
      resumo.textContent = '';
      resultados.innerHTML =
        '<div class="search-hint"><span class="search-hint-icon" aria-hidden="true">💡</span>' +
        '<div><strong>Comece a digitar.</strong><span>A busca olha o conteúdo das ' +
        'mensagens e o nome do contato. Os filtros acima funcionam sozinhos também.' +
        '</span></div></div>';
      return;
    }

    var meu = ++pedido;
    resultados.setAttribute('aria-busy', 'true');
    fetch(RESULTS_URL + '?' + params.toString(),
          {headers: {'X-Requested-With': 'XMLHttpRequest'}})
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (meu !== pedido) return;            // resposta velha: descarta
        resultados.removeAttribute('aria-busy');
        if (!d || !d.ok) {
          resumo.textContent = '';
          resultados.innerHTML = '<div class="search-empty"><span aria-hidden="true">⚠️</span>' +
            '<strong>Não foi possível pesquisar</strong>' +
            '<span>Tente de novo em alguns segundos.</span></div>';
          return;
        }
        if (d.aviso) {
          resumo.textContent = d.aviso;
          resultados.innerHTML = '';
          return;
        }
        var itens = d.itens || [];
        var partes = [];
        if (itens.length) {
          partes.push(
            itens.length < d.total
              ? 'Mostrando ' + itens.length + ' de ' + d.total + ' conversas'
              : itens.length + (itens.length === 1 ? ' conversa' : ' conversas')
          );
        }
        if ((d.filtros || []).length) partes.push(d.filtros.join(' · '));
        resumo.textContent = partes.join(' — ');
        resultados.innerHTML = itens.length
          ? itens.map(function (i) { return linha(i, d.termo); }).join('')
          : vazio(d.termo, true);
      })
      .catch(function () {
        if (meu === pedido) {
          resultados.removeAttribute('aria-busy');
        }
      });
  }

  function agendar() {
    window.clearTimeout(timer);
    timer = window.setTimeout(buscar, 300);
  }

  campoQ.addEventListener('input', agendar);
  campoQ.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { window.clearTimeout(timer); buscar(); }
    if (e.key === 'Escape') { campoQ.value = ''; buscar(); }
  });
  filtros.forEach(function (campo) {
    // Select responde na hora; texto e data esperam a digitação parar.
    campo.addEventListener(campo.tagName === 'SELECT' ? 'change' : 'input', agendar);
  });
  botaoLimpar.addEventListener('click', function () {
    campoQ.value = '';
    campoQ.focus();
    buscar();
  });
  botaoReset.addEventListener('click', function () {
    filtros.forEach(function (campo) { campo.value = ''; });
    buscar();
  });

  resultados.addEventListener('click', function (e) {
    var linhaEl = e.target.closest('[data-conversa]');
    if (!linhaEl) return;
    window.location.href = CONVERSAS_URL + '?conversa=' + linhaEl.dataset.conversa;
  });

  campoQ.focus();
}());
