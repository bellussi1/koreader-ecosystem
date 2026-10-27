# Roadmap do KOReader Ecosystem

[English](en/roadmap.md) | [Português (Brasil)](roadmap.md)

Atualizado em 26/10/2026. Este é o checklist ativo do projeto. Os planos antigos em `docs/superpowers/plans/` documentam etapas já executadas ou gates históricos; este roadmap é a referência para o que falta hoje.

## Issues do GitHub

O trabalho técnico foi dividido em issues com prioridade no título. Itens específicos da biblioteca ou do computador pessoal continuam fora do rastreador público.

### P0 — Integridade e publicação

- [#1 — Preparar a primeira release pública](https://github.com/bellussi1/koreader-ecosystem/issues/1)
- [#2 — Transferências MTP e recuperação dos sidecars](https://github.com/bellussi1/koreader-ecosystem/issues/2)
- [#3 — Validar o ciclo KOReader → Hardcover](https://github.com/bellussi1/koreader-ecosystem/issues/3)
- [#4 — Projeção revisável do histórico para o Calibre](https://github.com/bellussi1/koreader-ecosystem/issues/4)
- [#5 — Validar restauração isolada do perfil Restic](https://github.com/bellussi1/koreader-ecosystem/issues/5)
- [#6 — Portões de privacidade para publicar o repositório](https://github.com/bellussi1/koreader-ecosystem/issues/6)

### P1 — Integrações e automação

- [#7 — Interface de provedores de leitura](https://github.com/bellussi1/koreader-ecosystem/issues/7)
- [#8 — Paginação Hardcover com respeito à cota](https://github.com/bellussi1/koreader-ecosystem/issues/8)
- [#9 — Validar os limites de chamadas do app e plugin](https://github.com/bellussi1/koreader-ecosystem/issues/9)
- [#10 — Validar tarefas agendadas e automação USB no Windows](https://github.com/bellussi1/koreader-ecosystem/issues/10)
- [#11 — Matriz de compatibilidade e atualização verificada](https://github.com/bellussi1/koreader-ecosystem/issues/11)
- [#12 — Exportar dados disponíveis pela API Hardcover](https://github.com/bellussi1/koreader-ecosystem/issues/12)
- [#22 — Ampliar o editor Calibre e operações em lote](https://github.com/bellussi1/koreader-ecosystem/issues/22)
- [#23 — Retenção segura de builds Windows](https://github.com/bellussi1/koreader-ecosystem/issues/23)
- [#24 — Escolher biblioteca e backup na primeira execução](https://github.com/bellussi1/koreader-ecosystem/issues/24)

### P2 — Recursos futuros

- [#13 — Auditoria não destrutiva de EPUB](https://github.com/bellussi1/koreader-ecosystem/issues/13)
- [#14 — Sugestões revisáveis de metadados](https://github.com/bellussi1/koreader-ecosystem/issues/14)
- [#15 — Organização de metadados assistida por IA](https://github.com/bellussi1/koreader-ecosystem/issues/15)
- [#16 — Exportação seletiva para Obsidian e Anki](https://github.com/bellussi1/koreader-ecosystem/issues/16)
- [#17 — Estatísticas com proveniência dos dados](https://github.com/bellussi1/koreader-ecosystem/issues/17)
- [#18 — Avaliar OPDS e transferência por Wi-Fi](https://github.com/bellussi1/koreader-ecosystem/issues/18)
- [#19 — Avaliar assistente com proteção contra spoilers](https://github.com/bellussi1/koreader-ecosystem/issues/19)
- [#20 — Prova de conceito Tauri](https://github.com/bellussi1/koreader-ecosystem/issues/20)
- [#21 — Migrar fluxos exclusivos do PySide6 antes de removê-lo](https://github.com/bellussi1/koreader-ecosystem/issues/21)

## Estado verificado

- A versão **0.3.7** prepara o primeiro pacote deste repositório. Consulte [Releases](https://github.com/bellussi1/koreader-ecosystem/releases) para os downloads disponíveis e [Actions](https://github.com/bellussi1/koreader-ecosystem/actions) para os resultados de CI.
- A publicação exige auditoria de privacidade, rotação de credenciais expostas e verificação dos artefatos.
- Backup remoto e restauração independente continuam pendentes e exigem configuração explícita.

## P0 — concluir o fluxo seguro de uso diário

- [x] **Interface:** Inter na interface, Lora nos títulos e alvos de 48 px em ordenação/filtro, com clique no controle inteiro e sem sobreposição. Testes do renderer passaram. A instalação Windows 0.2.8 abriu com o ícone esperado.
- [x] **Calibre — prévia:** o fluxo gera candidatos em modo somente leitura para revisão antes de qualquer escrita.
- [ ] **Fluxo geral de transferência MTP:** validar lotes e reconexão além das transferências individuais já verificadas.
- [ ] **KOReader — restauração de sidecars:** validar recuperação e conferência dos arquivos após interrupções no dispositivo físico.
- [x] **Desempenho da prévia:** KOReader–Calibre copia apenas estatísticas e metadados de leitura, com verificação de origem.
- [ ] **KOReader → Hardcover:** validar no Kindle físico vínculo da edição, início, progresso, datas, conclusão e recuperação após ficar offline; registrar resultado sem dados pessoais desnecessários.
- [ ] **Sincronização de histórico:** regras de fonte, datas, progresso, status, notas, avaliações, anotações e identidade de edição estão documentadas; conflito `complete` com progresso abaixo de 100% agora bloqueia projeção. Ainda falta gerar uma prévia nova após a leitura física, revisar candidatos, criar backup e aplicar/verificar os vínculos exatos no Calibre.
- [ ] **Recuperação do perfil:** testes cobrem instalação isolada, checkpoint, reabertura e migração de schema. Ainda falta validar a interface com um snapshot Restic real.
- [ ] **Primeira release deste repositório:** validar CI, Setup, ZIP e hashes; o instalador ainda não tem assinatura digital.
- [x] **Atalho instalado:** os eventos `--squirrel-install` e `--squirrel-updated` recriam o atalho com o ícone do aplicativo.
- [ ] **Limpeza de builds locais:** definir retenção e proteger o pacote atual, o rollback e os assets de release; veja a issue #23.

## P1 — integrações e automação opcional

- [x] Fundação CSV: parser de formatos compatíveis, armazenamento local revisável, prévia paginada e exportação Goodreads com proteção de fórmulas e resenhas opcionais.
- [x] Sugerir correspondências locais para CSV por ISBN/ID da plataforma ou título + autor; conflitos ficam explícitos, e nada é vinculado nem alterado. A busca usa apenas os catálogos já carregados, sem novas chamadas remotas.
- [x] **Mapeamento de colunas CSV:** sugestões para cabeçalhos em inglês e PT-BR, associação manual de título/autor obrigatória e revisão dos campos opcionais. Nenhuma linha é pré-visualizada ou salva antes da confirmação dessa associação; o digest impede importar um arquivo que mudou depois da inspeção.
- [ ] Definir uma interface de provedores e automatizar apenas integrações com API oficial/documentada; nunca presumir equivalência entre status, edição, notas e avaliações.
- [x] **Reconciliação da outbox Hardcover:** a Configuração mostra ações seguras e ações para revisão manual. Exige confirmação, processa até 4 por clique e usa claim atômico. Ações nunca enviadas podem ser enviadas uma vez; envios incertos só repetem atualizações idempotentes de leitura existente/avaliação. Início de leitura e nota de diário ambíguos nunca são reenviados automaticamente.
- [x] **Revisão individual da outbox:** Configuração lista cada ação ambígua com livro quando disponível no cache, tipo, estado e horário. O texto de diário não sai do backend. Após conferir a conta, a pessoa pode registrar que a ação já aparece ou encerrá-la apenas localmente; isso não faz chamada nem altera o Hardcover. A migração SQLite v5 persiste a decisão e impede uma repetição concorrente.
- [x] **Cache offline Hardcover:** páginas carregadas e leitura atual são persistidas e podem ser exibidas por até 30 dias após falhas temporárias; a interface indica quando os dados vêm do cache.
- [ ] **Paginação em segundo plano:** ainda não há prefetch incremental separado da carga da biblioteca. Evitar chamadas extras e manter o orçamento do serviço.
- [ ] **Limites de chamadas do plugin:** o código do app limita 1.000 chamadas/24h e o patch do plugin 2.000/24h, totalizando no máximo projetado de 3.000; falta validar a instalação/contador no Kindle e observar os limites compartilhados com outros clientes do Hardcover.
- [x] Adicionar seleção explícita EN/PT-BR, usar o idioma do Windows na primeira abertura e incluir capturas em inglês com dados fictícios no README.
- [ ] **Automação local — implementação em revisão:** há preferências desligadas por padrão, tarefa diária do Windows, backup USB após duas detecções estáveis, trava entre processos para backups, gravações Calibre/MTP e recuperação do perfil, além da recuperação de execuções interrompidas. Testes locais cobrem concorrência, desativação sem interromper execução ativa e recuperação. Falta validar o pacote Squirrel/Task Scheduler no Windows em execução real; nenhuma tarefa foi registrada e as opções permanecem desligadas.
- [ ] Criar matriz de compatibilidade e atualização verificada; atualização automática aguarda assinatura digital e rollback confiável.
- [ ] Exportar somente os dados que a API Hardcover realmente fornece, sem apresentar o cache como backup completo da conta.

## P2 — recursos após a integridade dos dados

- [ ] Auditoria de EPUBs e sugestões de correção sem substituir os arquivos originais.
- [ ] Enriquecimento de metadados por ISBN, título e autor com candidatos e revisão explícita.
- [ ] Organização de metadados assistida por IA, sempre revisável e sem inventar informações.
- [ ] Exportação de notas para Obsidian e cartões Anki com seleção criteriosa.
- [ ] Estatísticas que identifiquem dados medidos, calculados e estimados.
- [ ] Avaliar catálogo OPDS e transferência por Wi-Fi após revisão de segurança e compatibilidade.
- [ ] Avaliar assistente contextual com limites contra spoilers e transparência de custos.
- [ ] Fazer uma prova de conceito Tauri comparando tamanho, instalação e compatibilidade antes de propor migração.
- [ ] Remover a UI PySide6 antiga somente depois de inventariar e migrar todos os fluxos exclusivos.

## Portões que precisam de ação ou decisão do usuário

- Backup remoto continua adiado até o usuário escolher um destino e uma restauração independente ser validada fora deste computador.
- Escritas no Calibre e no Kindle exigem estado do dispositivo conhecido, backup válido e as aprovações específicas já registradas; mudanças ambíguas permanecem pendentes.
- Abrir o repositório ao público só depois de concluir o scanner do histórico e a auditoria de privacidade. Credenciais compartilhadas fora do repositório devem ser rotacionadas antes da divulgação pública.
- Leitura real, avaliação e conclusão de um livro exigem ação no Kindle e conexão com a conta Hardcover; o desktop pode ficar fechado durante o acompanhamento do plugin.

## Princípios

- Calibre, KOReader e Hardcover preservam seus próprios dados; toda escrita entre serviços é revisável e verificada.
- A edição escolhida no Hardcover não substitui nem altera o arquivo digital local.
- Livros e sidecars existentes não são sobrescritos por padrão.
- Resultados medidos, calculados e estimados são identificados separadamente.
- Nenhuma biblioteca pessoal, credencial, log privado ou backup entra no código, nas capturas ou nos artefatos de release.
