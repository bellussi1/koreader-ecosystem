# Como contribuir

Todas as pessoas participantes devem seguir o [código de conduta](CODE_OF_CONDUCT.pt-BR.md). Consulte as [issues para primeira contribuição](https://github.com/bellussi1/koreader-ecosystem/labels/good%20first%20issue) para encontrar tarefas de escopo limitado.

[English](CONTRIBUTING.md) | [Português (Brasil)](CONTRIBUTING.pt-BR.md)

Obrigado pelo interesse no KOReader Ecosystem. O código original do projeto está disponível sob a [licença MIT](LICENSE). Softwares e recursos de terceiros mantêm suas próprias licenças; consulte os [avisos de terceiros](licenses/THIRD-PARTY-NOTICES.txt).

## Princípios do projeto

- Mantenha Calibre, KOReader e Hardcover como fontes de verdade distintas. Não copie silenciosamente status, edição, datas, avaliações ou progresso entre eles.
- Não associe livros usando apenas um título ambíguo. Preserve a identidade da edição e solicite revisão quando a correspondência não for segura.
- Use APIs documentadas e protocolos de dispositivo documentados. Não adicione scraping ou endpoints não documentados como alternativa.
- Mantenha credenciais no gerenciador seguro do sistema operacional. Nunca inclua tokens, senhas, dados pessoais da biblioteca, exportações do dispositivo ou conteúdo de backup no código, logs, fixtures, issues ou pull requests.
- Use fixtures sintéticas nos testes. Nunca inclua ebooks, bancos Calibre, sidecars `.sdr` do KOReader, exportações pessoais, manifestos locais ou snapshots de backup no Git.
- Descreva a validação de hardware com precisão. Testes com mocks não equivalem a um teste em um Kindle físico.

## Ambiente de desenvolvimento

O alvo desktop suportado é Windows x64. Instale Git, PowerShell 7, Node.js 24 ou posterior com npm e Python 3.14 com `uv`.

Na raiz do repositório:

```powershell
uv sync --all-groups
npm --prefix desktop ci
```

A instalação das dependências do desktop também instala os hooks Git do Husky. Cada commit verifica testes, lint, formatação e tipos de Python e desktop nos arquivos de trabalho; mantenha as dependências instaladas e corrija as falhas antes de tentar novamente. Execute `npm --prefix desktop run prepare` para reinstalar os hooks. O pacote Windows e a varredura de segredos continuam no CI; passar no hook não garante que o CI passe.

O Calibre é necessário para trabalhar com uma biblioteca Calibre real. Um Kindle físico só é necessário para validações de dispositivo por USB/WPD/MTP. Os testes e a preparação do ambiente não devem modificar dados reais da biblioteca ou do Kindle.

## Verificações

Execute as verificações relevantes para a sua alteração antes de abrir um pull request:

```powershell
uv run pytest -q
uv run ruff check .
uv run ruff format --check src tests
uv run mypy
npm --prefix desktop test -- --run
npm --prefix desktop run typecheck
npm --prefix desktop run lint
```

O GitHub Actions executa três verificações nos pull requests: **Python checks** roda testes, Ruff e mypy; **Windows desktop and package** testa a interface e a comunicação e gera o pacote Windows; **Secret scan** verifica o histórico Git. Um novo commit cancela execuções antigas do mesmo pull request; pushes, varreduras agendadas e execuções manuais continuam independentes. O workflow separado **Windows release** roda nas tags de versão e publica instalador, ZIP e hashes depois das verificações. Ele não cancela uma release em andamento.

Para investigar uma falha, abra a verificação no pull request e expanda a etapa que falhou. Execute localmente o comando correspondente acima. Não compartilhe dados privados da biblioteca nem credenciais em logs ou comentários. As verificações locais e o CI não substituem testes em um Kindle físico.

Para gerar o pacote desktop do Windows:

```powershell
npm --prefix desktop run make
```

Consulte o [guia de release](docs/releasing.md) para detalhes de versão e empacotamento.

## Pull requests

- Alterações na `main` passam por pull request com as verificações obrigatórias aprovadas e conversas de revisão resolvidas. Mantenha sua branch atualizada; os merges usam squash.
- O responsável padrão pelo código está em [CODEOWNERS](.github/CODEOWNERS). A aprovação do responsável não é obrigatória atualmente, mas mudanças em credenciais, backups, Calibre ou gravações no dispositivo exigem revisão cuidadosa.
- O Dependabot propõe atualizações semanais para GitHub Actions, dependências npm do aplicativo e o arquivo de bloqueio uv do Python. Cada proposta passa pelas mesmas verificações e revisão das demais alterações; não há merge automático.
- Mantenha cada alteração focada e explique o comportamento visível ao usuário, a decisão de projeto e as limitações.
- Inclua ou atualize testes para mudanças de comportamento, usando dados sintéticos.
- Atualize juntas as documentações em inglês e português do Brasil quando mudar fluxos suportados ou comportamento visível ao usuário.
- Não inclua caminhos pessoais, nomes de usuário, credenciais, identificadores de dispositivo, dados de livros ou detalhes de auditoria local no diff.
- Preserve as proteções de backup e rollback nas gravações no Calibre ou no Kindle.
- Informe quais verificações foram executadas e diferencie verificações automatizadas da validação em dispositivo físico.
- Não misture instaladores gerados, dados de usuários ou limpezas não relacionadas à alteração de código.

## Política de idioma da documentação

O `README.md` na raiz é a entrada em inglês. A entrada em português do Brasil é `README.pt-BR.md`; os guias ativos de uso e desenvolvimento existem em `docs/en/` e `docs/`, respectivamente. Mantenha os links de idioma perto do topo e atualize as duas cópias na mesma alteração. Planos históricos de implementação e registros operacionais específicos do proprietário não são guias oficiais para contribuidores e não são traduzidos automaticamente; revise-os quanto à privacidade antes de publicá-los.

## Relato de problemas

Use os formulários de bug ou solicitação de funcionalidade do repositório e compartilhe uma reprodução mínima com dados sintéticos. Não anexe biblioteca, ebook, banco Calibre, pasta `.sdr`, backup, log, captura de tela, token ou senha que contenha dados pessoais ou segredos. Para problemas de segurança, siga a [política de segurança](SECURITY.pt-BR.md).
