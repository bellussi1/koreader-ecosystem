# Regras de reconciliação do histórico de leitura

[English](en/reading-history-reconciliation.md) | [Português (Brasil)](reading-history-reconciliation.md)

Este documento define como o KOReader, o histórico local, o Calibre e o Hardcover podem compartilhar dados sem apagar histórico ou confundir edições.

## Fonte e destino de cada informação

| Informação | Regra |
| --- | --- |
| Eventos de página e mudanças de sidecar | Guardados como eventos locais imutáveis. Uma nova importação acrescenta eventos; não reescreve os anteriores. |
| Progresso atual | O snapshot atual do KOReader é a fonte do valor atual. Nunca usamos o maior percentual histórico. Uma redução em relação ao último valor aplicado fica pendente de revisão porque pode indicar reinício de leitura, releitura ou snapshot antigo. |
| Status | Usamos somente o status explícito do KOReader (`new`, `reading`, `complete` ou `abandoned`). 100% de progresso, sozinho, não conclui um livro. `complete` junto de progresso abaixo de 100% é conflito e bloqueia a projeção automática. |
| Datas | Mantemos localmente os horários observados em `page_stat_data` e `last_open`. Eles não provam a data real de início ou conclusão. Não inventamos datas para o Calibre ou Hardcover. |
| Sessões | Agrupamento com intervalo de inatividade de 30 minutos é uma estimativa, nunca uma sessão exata. |
| Avaliação e nota do KOReader | Permanecem no histórico local. Após revisão, podem ir para colunas KOReader próprias no Calibre. Não alteram avaliação, comentários ou diário do Hardcover. |
| Anotações | Versões ficam no histórico local. No Calibre, são acrescentadas por identificador estável; não removemos anotações existentes por ausência no dispositivo. Não enviamos texto de anotação ao Hardcover. |
| Avaliação, diário e status do Hardcover | Ações do usuário no Hardcover ou pelo plugin do KOReader. A importação do computador não reproduz essas ações nem faz chamadas remotas para nota, avaliação ou anotações. |

## Identidade de livro e edição

- A projeção para o Calibre exige vínculo confirmado à UUID exata do registro Calibre; título semelhante não basta.
- Edições em idiomas/formats distintos continuam como registros separados. Um EPUB PT-BR não tem seus metadados alterados para representar a edição física ENG.
- A edição Hardcover escolhida pelo usuário é preservada como vínculo explícito. A projeção local não troca essa edição nem a funde com a edição do EPUB.
- Divergência entre valores atuais e valores previamente sincronizados exige revisão antes de escrever.

## Aplicação segura

1. Gerar uma prévia nova do dispositivo conectado e conferir vínculos, conflitos e campos.
2. Confirmar Calibre fechado e `metadata.db` íntegro.
3. Criar um snapshot Restic de Calibre e estado local; conferir o repositório antes de escrever.
4. Aplicar apenas a prévia revisada, reler os campos pela UUID e conferir novamente a integridade do banco.

Um preview antigo não é prova do estado atual do Kindle. A leitura local não atualiza o Hardcover; o plugin instalado no KOReader continua responsável pela sincronização direta com o serviço quando conectado à internet.
