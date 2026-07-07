# Padrões Codex do Projeto

Este arquivo define os padrões de trabalho para o projeto BEEZAP.

## Regras principais

1. Toda alteração feita no projeto deve ser acompanhada de um commit no Git.
2. Após o commit, deve ser feito `push` para o repositório remoto.
3. Toda mudança relevante deve ser registrada no `docs/HISTORICO.md` com uma descrição curta **e** a documentação afetada deve ser atualizada no mesmo commit — principalmente o `docs/CONTEXTO.md` (arquitetura, telas, endpoints, comandos, pendências) e, quando fizer sentido, `WAPI_MEDIA_INTEGRATION.md`/`DEPLOY.md`. **A documentação deve refletir sempre o estado atual do projeto.**
4. Antes de concluir uma etapa, validar o projeto com os comandos apropriados.
5. Manter a estrutura do projeto organizada e evitar arquivos soltos na raiz.
6. O sistema deve ser didático, simples de usar e pensado para pessoas com pouca experiência técnica.
7. O acesso deve ser controlado por perfil de usuário, liberando botões e funções conforme o nível logado.
8. A evolução do sistema deve ser feita com calma, por partes, sempre preservando uma base sólida e funcional.

## Padrões de interface e CSS

1. O sistema deve manter interface simples, clara e amigável para usuário final.
2. Evitar termos técnicos desnecessários nas telas.
3. Mensagens de sucesso, erro, aviso e informação devem usar o padrão visual de notificação suspensa/toast do sistema.
4. Não criar alertas grandes dentro do conteúdo da página, salvo se for uma exceção explicitamente solicitada.
5. Mensagens para usuário final devem ser curtas e compreensíveis.
6. Não exibir dados técnicos sensíveis nas mensagens, como token, payload, headers, traceback, resposta bruta de API ou IDs técnicos desnecessários.
7. Quando uma página tiver estilos próprios, criar um CSS específico para ela.
8. O arquivo `dashboard.css` deve ficar reservado para estilos gerais do painel.
9. CSS específico de página deve usar uma classe raiz própria para evitar conflito com outras telas.
10. Exemplo de organização:
   - `static/css/dashboard.css` para base geral do painel
   - `static/css/wapi_settings.css` para tela WhatsApp / W-API
   - futuras telas podem ter CSS próprio quando necessário
11. Não misturar regras muito específicas de uma tela dentro do CSS global, quando isso puder afetar outras páginas.
12. Antes de concluir ajuste visual, validar no navegador quando possível.
13. Após alterar CSS, testar com recarregamento forçado, como Ctrl + F5, para evitar cache.
14. O layout deve ser responsivo e não deve criar rolagem horizontal.
15. Cada tela deve evitar poluição visual e excesso de botões.
16. Elementos não editáveis não devem receber foco de texto nem exibir barra piscando de digitação; o caret deve aparecer somente em campos editáveis reais, como `input`, `textarea` ou `contenteditable` necessário.

## Padrão de notificações

1. Usar o padrão toast para notificações do sistema.
2. A notificação deve ser pequena, discreta e bonita.
3. Deve funcionar para sucesso, erro, aviso e informação.
4. Deve ter texto simples para usuário final.
5. Deve desaparecer automaticamente ou permitir fechamento manual, se isso já estiver disponível no padrão do sistema.
6. Não usar notificações como card grande no meio da página sem pedido explícito.

## Padrão de commit

- Usar mensagens curtas, claras e descritivas.
- Preferir o formato:
  - `feat: ...` para novas funcionalidades
  - `fix: ...` para correções
  - `style: ...` para ajustes visuais
  - `docs: ...` para documentação
  - `chore: ...` para tarefas de manutenção

## Fluxo esperado

1. Fazer a alteração.
2. Registrar no `docs/HISTORICO.md` **e atualizar a documentação afetada** (ex.:
   `docs/CONTEXTO.md`) para refletir o novo estado.
3. Conferir o estado do projeto (`python manage.py check`, testes quando fizer sentido).
4. Criar o commit (código + docs juntos).
5. Fazer `push`.

## Observações

- Se houver dúvida sobre o impacto da mudança, primeiro validar localmente.
- Se a alteração mexer em interface, revisar o layout no navegador antes do commit.
- Não deixar mudanças importantes sem versionamento.
