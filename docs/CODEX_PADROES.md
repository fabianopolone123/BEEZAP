# Padrões Codex do Projeto

Este arquivo define os padrões de trabalho para o projeto BEEZAP.

## Regras principais

1. Toda alteração feita no projeto deve ser acompanhada de um commit no Git.
2. Após o commit, deve ser feito `push` para o repositório remoto.
3. Toda mudança relevante deve ser registrada no `docs/HISTORICO.md` com uma descrição curta.
4. Antes de concluir uma etapa, validar o projeto com os comandos apropriados.
5. Manter a estrutura do projeto organizada e evitar arquivos soltos na raiz.
6. O sistema deve ser didático, simples de usar e pensado para pessoas com pouca experiência técnica.
7. O acesso deve ser controlado por perfil de usuário, liberando botões e funções conforme o nível logado.
8. A evolução do sistema deve ser feita com calma, por partes, sempre preservando uma base sólida e funcional.

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
2. Registrar no `docs/HISTORICO.md`.
3. Conferir o estado do projeto.
4. Criar o commit.
5. Fazer `push`.

## Observações

- Se houver dúvida sobre o impacto da mudança, primeiro validar localmente.
- Se a alteração mexer em interface, revisar o layout no navegador antes do commit.
- Não deixar mudanças importantes sem versionamento.
