# Guia do aplicativo Windows

Este guia descreve a versão Electron **0.3.8**. Consulte a [página de Releases](https://github.com/bellussi1/koreader-ecosystem/releases) para os instaladores disponíveis.

## Preparar

1. Abra **KOReader Ecosystem** pelo menu Iniciar ou pelo atalho da Área de Trabalho.
2. Deixe a janela do Calibre fechada quando for adicionar ou editar livros. O aplicativo usa as ferramentas oficiais do Calibre e cria um snapshot Restic antes de gravar.
3. Abra **Configuração** e conecte uma chave nova do Hardcover. A chave é validada e guardada no Gerenciador de Credenciais do Windows. Chaves que foram coladas em conversas devem ser revogadas no Hardcover.
4. Aguarde a carga das estantes. Sem conexão Hardcover, a biblioteca Calibre continua disponível.

## Biblioteca Calibre

- A estante **Calibre** mostra os livros da biblioteca local, com capas, formatos, série, ISBN, busca e ordenação.
- **Adicionar livro** abre a janela de arquivos do Windows. Escolha um arquivo; revise o título, autor e possíveis duplicatas. Selecione **Anexar formato** para um registro candidato ou **Criar livro separado** para criar outro registro. A decisão nunca é automática. A versão atual adiciona um arquivo por vez.
- **Editar** abre os campos bibliográficos cobertos: título, autores, série, editora, data, idiomas, tags, avaliação, identificadores, capa e campos personalizados de valor único compatíveis. O app faz backup, grava pelo `calibredb` e relê o catálogo. Campos personalizados múltiplos/calculados e comentários não fazem parte do editor. As alterações não mudam a edição ou o status no Hardcover.
- Para alterar tags de vários livros, selecione os registros, revise a lista e as tags e confirme o backup antes da aplicação. A releitura do catálogo confere os resultados.

## Estantes Hardcover

Use **Quero ler**, **Lendo** e **Lidos** para consultar os status da sua conta. A caixa **eBook / Kindle** começa marcada, conforme sua preferência por edições digitais; desmarque para incluir outros formatos. A estante é carregada em páginas automáticas, sem botão “carregar mais”.

O cartão **Lendo agora** mostra o registro de leitura atual que teve a atualização mais recente no Hardcover. O progresso vem do KOReader/plugin sincronizado diretamente com o Hardcover, não de uma leitura do arquivo no computador. Consultas que falham por indisponibilidade temporária são repetidas automaticamente até duas vezes; falha de autenticação não é repetida. Metadados do Calibre continuam separados dos registros remotos.

Se uma ação do Hardcover ficar pendente após uma falha, abra **Configuração → Conexão Hardcover**. A fila separa ações seguras das que exigem conferência. **Processar até 4 ações seguras** pede confirmação antes do envio; as demais permanecem na fila. A tela de revisão mostra ação, livro (quando disponível no cache), estado e horário, mas nunca o texto das notas de diário. Confira a conta Hardcover e escolha **Já aparece no Hardcover** ou **Encerrar sem confirmar**. A escolha só fecha o item local e não altera a conta. Ações nunca enviadas são enviadas uma vez; um início de leitura ou nota de diário com envio incerto não é repetido para evitar duplicatas. Atualizações incertas de uma leitura existente e de avaliação podem ser repetidas com segurança.

Em **Kindle e sincronização**, o aplicativo mostra a última consulta bem-sucedida às estantes nesta sessão e avisa quando a consulta atual falha. Esse horário confirma que o aplicativo leu o Hardcover; não confirma o envio do progresso pelo Kindle. O envio ocorre do KOReader via Wi-Fi e só pode ser conferido no próprio Hardcover.

## Histórico CSV

Na **Biblioteca**, escolha **Histórico CSV**, selecione Goodreads, StoryGraph ou Maratona como origem e escolha um CSV. Confira o mapeamento sugerido (título e autor são obrigatórios), ajuste os cabeçalhos e clique em **Pré-visualizar com estas colunas**. Resenhas ficam desmarcadas por padrão; mapeie essa coluna explicitamente se quiser importá-las. Só valores das colunas mapeadas são guardados; colunas ignoradas e notas privadas são descartadas. Revise contagens, avisos e livros antes de confirmar. As importações confirmadas ficam no banco local e não atualizam o Hardcover nem o Calibre.

Selecione um lote em **Importações recentes** para exportar um CSV compatível com Goodreads pela janela de salvamento do Windows. Resenhas que você mapeou ficam de fora por padrão e só entram se você marcar a opção. Notas privadas nunca são importadas nem exportadas. Para evitar misturar dados, o mesmo arquivo e origem não podem ser reimportados com outro mapeamento; o lote anterior é mantido. A troca CSV ainda não associa as linhas às edições do Hardcover/Calibre nem sincroniza alterações automaticamente.

### Limites de chamadas do Hardcover

O aplicativo Windows bloqueia novas chamadas ao atingir **1.000 chamadas em qualquer janela móvel de 24 horas** e **25 por minuto**. O contador é local, persistente e conta tentativas antes da conexão, inclusive as que falham. No Kindle, o plugin atualizado tem uma cota independente de **2.000 chamadas em 24 horas** e **25 por minuto**, exibida no menu Hardcover do KOReader. Assim, os dois componentes deste projeto ficam limitados a 3.000 chamadas por dia e 50 por minuto.

Na página oficial da API consultada, o Hardcover informa limite de 60 chamadas por minuto, mas não especifica uma cota diária. As cotas locais descritas acima não contabilizam o site nem outros clientes da conta; portanto, não garantem o consumo total nem a elegibilidade do plano. [Documentação da API Hardcover](https://docs.hardcover.app/api/getting-started/).

Os cards do Calibre e do Hardcover ficam em estantes separadas; ainda não há vínculo automático entre um arquivo local e uma edição Hardcover. No KOReader, selecione manualmente no plugin a edição que deve receber o progresso. Se a edição em português e a edição original tiverem paginação diferente, a porcentagem pode divergir; não escolha uma edição só pela capa sem conferir se ela corresponde à leitura que quer registrar.

## Enviar ao Kindle

1. Conecte o Kindle por USB e espere o Windows mostrar a tela **USB conectado**. A aplicação verifica a presença do aparelho automaticamente a cada 15 segundos; você também pode usar **Verificar agora**. Feche ou saia do KOReader durante a transferência e não desconecte o cabo.
2. Na estante **Calibre**, escolha **Enviar** no livro desejado.
3. Escolha um formato já presente no Calibre e revise o nome que ficará no Kindle.
4. Confirme **Fazer backup verificado e enviar**. O aplicativo cria e verifica um snapshot Restic da biblioteca e do Kindle, copia apenas o arquivo escolhido para `documents/Books` e tenta reler a cópia para comparar SHA-256.
5. Ejete o Kindle pelo Windows antes de desconectar. Abra o livro no KOReader depois da ejeção.

O envio nunca sobrescreve. Um arquivo já existente com conteúdo idêntico é informado como já enviado; um nome em colisão exige que você escolha outro nome. A interface mostra resultado incerto ou divergência para você conferir o aparelho antes de tentar novamente.

**Limite de validação:** transferências individuais foram verificadas em um Kindle físico. O fluxo geral de lotes, reconexão e restauração de sidecars KOReader ainda precisa de validação.

## Acompanhamento de leitura

Para atualizar progresso, datas de início/fim e status pelo KOReader, o Kindle precisa estar fora do modo USB, com KOReader aberto e Wi-Fi conectado. O plugin conversa diretamente com o Hardcover; o aplicativo Windows pode estar fechado. Depois da leitura, atualize a Biblioteca para buscar os dados remotos. Notas e avaliações continuam sendo ações manuais suas.

## Importar histórico local do KOReader

Com o Kindle conectado em modo USB, **Kindle e sincronização** pode ler uma cópia temporária de `statistics.sqlite3` e dos metadados de leitura `.sdr` do KOReader. A prévia não grava no Kindle. Ela mostra eventos de página, posição e progresso disponíveis, status, avaliação, nota e quantidade de anotações. Só **Importar histórico local** confirma e grava os dados no SQLite privado do aplicativo.

O KOReader não registra IDs de sessão. O app agrupa eventos separados por até 30 minutos e identifica essas sessões como **estimadas**. A importação fica no computador e não envia nem atualiza dados no Hardcover. A sincronização online do plugin KOReader–Hardcover continua sendo um fluxo separado, feito pelo Kindle via Wi-Fi.

Sidecars sem vínculo exato permanecem separados. Se o KOReader não fornecer um título nos metadados, a prévia usa o nome da pasta `.sdr` e identifica essa origem; esse nome serve apenas para revisão visual e nunca cria um vínculo automático.

Para projetar o histórico no Calibre, vincule manualmente cada livro do KOReader ao registro/edição local correto e revise a prévia. Não há correspondência automática por título. Aplicar a prévia exige Calibre fechado, backup Restic verificado e leitura dos valores após a gravação. Os campos projetados são progresso, status, posição, avaliação, nota e anotações KOReader; eles não alteram a estante ou a edição no Hardcover.

O banco local de histórico entra nos snapshots Restic allowlisted quando existe. Confira se o destino configurado fica fora do disco de origem. O fluxo ainda precisa ser validado com uma leitura real no Kindle antes de ser considerado pronto para uso diário.

### Checklist de aceitação no Kindle físico

Esta validação ainda está pendente. Faça-a com um EPUB de teste cuja edição local esteja identificada corretamente:

- [ ] Leia algumas páginas no KOReader e registre uma nota ou anotação de teste; observe o progresso e os horários exibidos pelo próprio leitor.
- [ ] Conecte o Kindle em modo USB e gere a prévia local; confira o livro, progresso, status, nota, anotações e avisos antes de confirmar.
- [ ] Confirme a importação local e verifique se os eventos de página e a revisão da anotação aparecem após reabrir o aplicativo.
- [ ] Vincule manualmente o livro à edição correta do Calibre; com o Calibre fechado, revise os campos propostos e só então aplique a projeção com backup verificado.
- [ ] Restaure um snapshot para uma pasta nova e confira que o banco local recuperado contém o lote, eventos, vínculo e anotações sem substituir o perfil ativo.
- [ ] Se também estiver validando a integração online, ejete o Kindle, conecte o Wi-Fi e confira no Hardcover se o plugin KOReader atualizou a edição esperada. Esse envio é separado da importação local.

## Backups e disponibilidade

- Alterações Calibre e envio ao Kindle exigem backup local antes da escrita.
- Em **Kindle e sincronização**, crie snapshots Restic da biblioteca Calibre e do estado local do app ou, quando detectado, também do Kindle. No código em desenvolvimento, o estado allowlisted inclui `application-state.sqlite3`, onde fica o histórico KOReader importado, além da configuração sem credenciais, do contador Hardcover (somente timestamps) e do histórico operacional sanitizado. O backup exige o Calibre fechado e `metadata.db` íntegro; a verificação restaura temporariamente o snapshot e compara os arquivos.
- A restauração do Electron cria uma pasta nova dentro do diretório escolhido, valida o manifesto e mostra o caminho de destino. Ela não substitui a biblioteca ativa nem aplica automaticamente o estado recuperado ao perfil atual.
- A senha pode ser cadastrada em **Configuração** e fica no Gerenciador de Credenciais do Windows. Ao trocar uma senha já usada, o aplicativo testa a candidata contra o repositório antes de gravá-la; se falhar, conserva a credencial anterior.
- O destino remoto R2/S3 continua adiado até a escolha de um destino e a validação de uma restauração independente fora deste computador. O repositório Restic atual permanece no mesmo computador e não protege contra perda do disco físico.
- O aplicativo não precisa ficar aberto o tempo todo. Ele precisa rodar no Windows para usar a biblioteca local, criar snapshots ou transferir arquivos USB/MTP. Um serviço hospedado não consegue acessar diretamente esses recursos.

### Automação opcional

Em **Configuração → Automação local**, as opções começam desligadas:

- **Backup diário** registra uma tarefa no Agendador do Windows para salvar Calibre e dados locais do app. Ela roda somente na sessão do Windows do usuário, com o computador acordado, e exige o Calibre fechado. A tarefa usa o lançador estável da instalação e não abre janela nem terminal.
- **Ao conectar por USB** só funciona enquanto o KOReader Ecosystem está aberto. A aplicação exige duas verificações consecutivas do dispositivo. Se o aparelho já estiver conectado ao abrir o app e a opção estiver ativa, o backup começa após a segunda verificação. Se você ativar a opção enquanto ele já está conectado, o backup aguarda a próxima desconexão e reconexão. Esse backup inclui o dispositivo e os dados locais; uma tentativa com falha só será repetida numa nova conexão.
- Backups, gravações protegidas no Calibre/Kindle e recuperação do perfil usam uma trava entre processos. Uma operação concorrente recebe um aviso para aguardar e tentar novamente; nenhuma cópia em andamento é encerrada à força.
- Os backups automáticos validam o snapshot, mas não fazem a restauração temporária completa a cada execução. Use **Criar e verificar backup** para validar também a restauração.
- Desativar uma opção remove as execuções futuras. Um backup que já começou termina; a desativação não encerra um processo no meio da cópia.

O pacote Windows e a execução real no Agendador ainda precisam de validação antes de considerar esse fluxo pronto para uso diário. As opções permanecem desligadas até a pessoa ativá-las.
