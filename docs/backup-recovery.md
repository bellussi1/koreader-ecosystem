# Backup e recuperação

## Pré-requisitos

- Feche o Calibre e o servidor do Calibre. Para incluir o Kindle no snapshot, conecte-o por USB e mantenha a tela USB aberta; o backup Calibre funciona sem o dispositivo.
- Instale as dependências do projeto com `scripts/install.ps1`.
- Instale o Restic com `winget install --exact --id restic.restic --scope user` ou pelo [binário oficial](https://restic.readthedocs.io/en/stable/020_installation.html#official-binaries).
- Guarde a senha única do repositório em um gerenciador de senhas fora deste computador. O projeto mantém uma cópia local no Gerenciador de Credenciais do Windows para uso do CLI; perder a senha significa perder o acesso aos snapshots. No Windows, abra **Painel de Controle → Contas de Usuário → Gerenciador de Credenciais → Credenciais do Windows → Credenciais Genéricas** e procure `koreader-ecosystem` (usuário `restic`). O Credential Manager não exibe o valor salvo; para recuperação, consulte o gerenciador externo.

## Usar o Electron 0.2.8

Na versão 0.2.8, os snapshots incluem a biblioteca Calibre, o estado local do aplicativo e, se selecionado, o Kindle. O estado local cobre a configuração sem credenciais, o banco KOReader–Calibre, o contador de chamadas Hardcover com somente timestamps, o histórico de operações validado e `application-state.sqlite3`. Tokens, senha Restic, cache temporário, staging e cópias de rollback do plugin ficam de fora. Na página **Kindle e sincronização**, escolha o escopo e use **Criar e verificar backup**. O aplicativo exige o Calibre fechado e aprova `metadata.db` com `integrity_check`. A verificação restaura o snapshot temporariamente e compara os arquivos. Para restaurar, selecione um snapshot, escolha a pasta pai e confirme; o aplicativo cria uma pasta nova e exibe o destino. A restauração da biblioteca nunca substitui os arquivos ativos. Depois de uma restauração verificada, você pode revisar a prévia do perfil local e confirmá-la separadamente; o aplicativo cria um checkpoint do perfil atual, não importa credenciais e pede reinício. A atualização migra o perfil e as credenciais antigas sem apagar as cópias legadas.

Na primeira execução da versão 0.2.6, os registros locais legados são migrados sem apagar as fontes para `application-state.sqlite3`, que passa a ser incluído nos snapshots.

### Reabrir o perfil recuperado

1. Instale/abra o aplicativo no Windows em que o perfil será usado e deixe o Calibre fechado.
2. Em **Kindle e sincronização → Backup e restauração**, selecione o snapshot e escolha uma pasta pai nova, fora da biblioteca ativa e do repositório Restic. Confirme e aguarde a validação terminar.
3. Em **Recuperar perfil local**, selecione a pasta recém-restaurada. Confira a versão do schema e as contagens da prévia; só confirme se forem compatíveis com o estado que espera recuperar.
4. Confirme **Revisar e recuperar perfil**. O aplicativo guarda o perfil anterior em `%LOCALAPPDATA%\KOReaderEcosystem\profile-recovery-checkpoints\<checkpoint-id>` e substitui somente o `config.json` e `application-state.sqlite3` do perfil ativo. A pasta restaurada, a biblioteca Calibre e o Kindle não são modificados.
5. Clique **Reiniciar agora**. Se o reinício automático falhar, feche e abra o KOReader Ecosystem. Em **Configuração**, confira se a biblioteca Calibre aparece como conectada; em **Kindle e sincronização**, confirme que o histórico local esperado está presente. Mantenha a pasta restaurada e o checkpoint até terminar essa conferência.

A recuperação reinstala a configuração e o banco local, mas mantém os caminhos Calibre e Restic salvos no snapshot. No mesmo computador, esses caminhos precisam continuar disponíveis. A cópia Calibre restaurada permanece separada e não é escolhida automaticamente como biblioteca ativa. Se o caminho salvo não existir ou a biblioteca estiver em outra pasta, pare antes de editar livros ou criar backups: a versão Electron não oferece ainda seleção de um novo caminho Calibre/Restic pela interface. Não restaure sobre a biblioteca antiga. A senha Restic e o token Hardcover não são importados; eles continuam no Gerenciador de Credenciais do Windows e precisam estar disponíveis nesse perfil do Windows.

Esta sequência foi validada com um perfil sintético em diretório temporário: após simular a reabertura, o banco restaurado foi lido, o caminho de configuração foi preservado e os arquivos-fonte `config.json`/SQLite e um arquivo sentinela da biblioteca original permaneceram byte a byte iguais. Isso valida o fluxo do perfil, não uma recuperação após reinstalação nem uma cópia fora do computador.

Em **Configuração**, cadastre a senha no Gerenciador de Credenciais do Windows e mantenha uma cópia em outro gerenciador. Ao trocar uma senha já usada, o aplicativo valida a candidata no repositório antes de salvá-la. Restic está incluído no pacote Electron.

### Interface PySide6 e CLI legadas

Para usar a interface legada, abra `dist/KOReaderEcosystem-next/KOReaderEcosystem.exe` e mantenha os arquivos próximos dentro da mesma pasta. O guia [LEIA-ME-EXECUTAVEL.txt](LEIA-ME-EXECUTAVEL.txt) acompanha a pasta. A CLI continua disponível pelas instruções abaixo.

O destino padrão é `Documents\Calibre_Kindle_Backups\reading-ecosystem-restic`. Ajuste `READING_RESTIC_REPOSITORY` para outro diretório quando necessário. A biblioteca e o staging do Kindle precisam estar no mesmo volume para que os snapshots usem caminhos relativos e restaurem sem tentar reaplicar as permissões das pastas superiores do perfil Windows.

## Criar e validar um backup

Execute `uv run koreader-ecosystem doctor` para conferir Calibre, Kindle, Restic, Credential Manager e espaço livre. Com as pré-condições resolvidas, rode:

```powershell
uv run koreader-ecosystem backup --verify-restore
```

Confirme no prompt que a senha está guardada fora do computador. Se ainda não estiver no Credential Manager, o programa solicita a senha sem mostrá-la. O comando inventaria e copia o Kindle por MTP em modo somente leitura. Como o Kindle não fornece checksum pelo MTP, o adaptador compara SHA-256 de uma segunda leitura com o staging e reenumera o dispositivo. O Windows WPD não expõe de forma confiável os livros em documents, as pastas .sdr nem alguns arquivos em outras raízes do Kindle; esses itens são relidos por uma cópia temporária do Shell MTP. Os arquivos nas raízes system e koreader são lidos por WPD. Isso detecta divergências comuns, mas não é um checksum fornecido pelo Kindle nem uma prova contra erro idêntico nas duas transferências. O Shell também não oferece um sinal confiável de conclusão: o adaptador espera tamanho estável e acesso exclusivo. O tempo depende do volume; pode se aproximar de duas transferências completas, e o espaço temporário adicional chega ao tamanho do maior arquivo. O comando também valida o `metadata.db` do Calibre, cria o snapshot criptografado e executa `restic check`.

Com `--verify-restore`, o snapshot também é restaurado para uma pasta temporária nova. O programa compara caminhos, tamanhos, horários e SHA-256 dos arquivos restaurados com as fontes verificadas, incluindo o estado local do aplicativo. A restauração e as cópias temporárias são removidas ao final. A saída contém apenas o ID do snapshot e totais agregados.

O procedimento completo pode ser iniciado por `scripts/verify-live-backup.ps1`. Ele salva um relatório agregado em `%LOCALAPPDATA%\KOReaderEcosystem\reports`; o relatório não contém nomes de livros nem caminhos das fontes e não é versionado.

## Verificar um backup antes do uso

Depois de criar o primeiro snapshot, use **Verificar** ou a restauração temporária para conferir que o repositório e os arquivos restaurados estão íntegros. A verificação não substitui uma cópia em outro dispositivo físico.
## Espelhamento remoto S3 (opcional)

O backup remoto não é configurado automaticamente. Escolha um provedor compatível e configure o destino quando quiser manter uma cópia fora deste computador. Antes de ativar o recurso:

1. Habilite o armazenamento S3 no provedor e crie um bucket vazio. Crie uma chave limitada ao bucket e às operações necessárias para leitura e escrita de objetos.
2. Em um terminal local interativo, configure endpoint, caminho do repositório e região. No R2, o endpoint tem o formato `https://<ACCOUNT_ID>.r2.cloudflarestorage.com` e a região é `auto`.

```powershell
uv run koreader-ecosystem remote configure --repository s3:https://<ACCOUNT_ID>.r2.cloudflarestorage.com/<BUCKET>/reading-ecosystem --region auto
```

O comando solicita access key ID e secret key em prompts ocultos e armazena os valores no Gerenciador de Credenciais do Windows. A senha Restic existente é usada para criptografar o destino. Os campos de configuração remota contêm endpoint, bucket/caminho e região, sem as chaves.

3. Confira endpoint, bucket e região em `%LOCALAPPDATA%\KOReaderEcosystem\config.json`. Quando estiver pronto para enviar os dados, execute:

```powershell
uv run koreader-ecosystem remote sync
```

Se o repositório remoto ainda não existir, o comando o inicializa com os parâmetros de particionamento do repositório local, copia os snapshots, executa `restic check` e confirma que os metadados de todos os snapshots locais aparecem no remoto. O primeiro espelhamento lê e envia os snapshots completos, pode levar bastante tempo e consumir banda; cópias repetidas são retomáveis e snapshots já copiados são ignorados pelo Restic. Nenhuma chamada remota ocorre em `doctor`, `backup` ou nos demais comandos locais.

A sincronização remota ainda não foi executada neste projeto e a restauração remota independente ainda não foi validada. Até essa validação, o backup local continua sendo a única cópia configurada e o projeto não declara recuperação 3-2-1 concluída.

## Restaurar um snapshot

Liste os IDs disponíveis:

```powershell
uv run koreader-ecosystem status
```

Restaure para uma pasta absoluta que ainda não exista e fique fora da biblioteca, do repositório e de qualquer origem:

```powershell
uv run koreader-ecosystem restore --snapshot ID_DO_SNAPSHOT --target C:\Restauracao\reading-ecosystem
```

A árvore restaurada mantém os caminhos relativos gravados no snapshot; a biblioteca Calibre, a pasta `.kre-app-state-*` e a cópia do Kindle aparecem no destino conforme o escopo. Examine os totais e o conteúdo recuperado antes de substituir qualquer origem ativa. O estado do app permanece dentro da pasta recuperada até uma revisão manual; a aplicação não sobrescreve automaticamente o perfil ativo. O destino precisa ainda não existir; os diretórios pai que faltarem são criados. Se o Restic falhar ou a validação local do manifesto não concluir, a árvore parcial criada para essa tentativa é removida.

## Recuperação após reinstalar o Windows

1. Instale Python, `uv`, Git e Restic.
2. Clone o [repositório do projeto](https://github.com/bellussi1/koreader-ecosystem) e rode `scripts/install.ps1`.
3. Aponte `READING_RESTIC_REPOSITORY` para a cópia íntegra do repositório Restic.
4. Recupere a senha do seu gerenciador externo. O Credential Manager local será recriado no primeiro uso.
5. Rode `uv run koreader-ecosystem status`, depois restaure para uma pasta nova e confira os arquivos antes de reabrir o Calibre.

## Token Hardcover

A configuração local usa entrada oculta e o Credential Manager do Windows (serviço `koreader-ecosystem`, usuário `hardcover`). Na primeira inicialização após atualizar, as credenciais salvas com o identificador antigo são copiadas para o novo serviço e mantidas como cópia de retorno. Consulte o gerenciador externo para reconfigurar após reinstalar o Windows. A aplicação não imprime nem recebe o token por argumento.

O instalador prepara a versão upstream 0.4.0 com uma correção local determinística para o ciclo da leitura. Antes de aplicar a correção, confere o SHA-256 do ZIP; também rejeita mudanças inesperadas no código upstream. A correção cria a leitura e grava a data local quando o livro vinculado é aberto no KOReader, sincroniza a última página salva após a conexão Wi-Fi voltar e registra a data de conclusão quando o KOReader marca o documento como concluído. Se a posição Kindle e a posição Hardcover divergirem após uso offline, o KOReader pede qual posição manter. Para novas tentativas de leitura, cria um registro novo em vez de alterar a leitura concluída anterior.

O token continua necessário no Kindle para a API direta durante a leitura. O plugin upstream exige o token no arquivo `hardcover_config.lua` em texto simples no Kindle. Esse arquivo pode ser lido por quem tiver acesso físico aos arquivos do dispositivo. Para remover a integração, exclua manualmente a pasta do plugin do Kindle. A CLI remove sua cópia temporária local após a tentativa de instalação.

Para atualizar o plugin existente, use **Configuração → Atualizar plugin existente com backup verificado**. Deixe o Calibre fechado e o Kindle na tela USB. A ação cria um snapshot Restic de Calibre e Kindle, confere a integridade do repositório e valida uma restauração temporária antes de escrever no dispositivo. Ela exige que cada arquivo Lua alterado corresponda exatamente ao release 0.4.0 fixado, ao patch anterior distribuído pelo projeto ou à nova versão. Esses estados têm hashes fixados; qualquer outra versão encerra a operação antes da escrita. A cópia atual de cada arquivo é salva com seu caminho relativo em `%LOCALAPPDATA%\KOReaderEcosystem\plugin-backups`; no Kindle, cada arquivo que muda recebe uma cópia `.lua` de rollback no mesmo diretório. O app relê e compara por SHA-256 todos os arquivos atualizados. Se alguma etapa falhar, tenta restaurar os arquivos alterados em ordem reversa e valida os hashes. Configuração e arquivos que não fazem parte do patch permanecem intactos. Preserve o snapshot e a cópia local até o teste de leitura terminar.

O app Windows não precisa ficar aberto enquanto você lê. O Kindle precisa estar com o KOReader ativo, o plugin associado à edição correta, o rastreamento habilitado e Wi-Fi disponível para sincronizar. Sem rede, a posição fica salva localmente e será reconciliada na próxima conexão. Percentuais/páginas em EPUB podem ser aproximados quando o mapeamento da edição não está disponível. A conclusão automática só ocorre quando o KOReader sinaliza que o documento foi concluído; avalie e escreva notas depois pela Estante do aplicativo Windows.

## Limite do destino local

Confira a localização do destino configurado. A criptografia protege o conteúdo do repositório, mas uma falha ou perda física do disco pode eliminar fontes e backup juntos quando estão no mesmo disco. Uma cópia em outro disco ou armazenamento remoto precisa ser configurada e testada com uma restauração independente.
