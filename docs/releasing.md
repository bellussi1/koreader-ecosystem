# Publicar uma versão Windows

Cada versão publicada é identificada por uma tag SemVer (`vMAJOR.MINOR.PATCH`) e tem sua própria GitHub Release. A release contém o instalador, o ZIP portátil com o executável e seus arquivos de suporte, e os hashes SHA-256. Os binários não são commitados no histórico Git.

O workflow [`.github/workflows/release.yml`](../.github/workflows/release.yml) é acionado quando uma tag `v*` é enviada ao GitHub. Ele valida a versão, roda as verificações, monta o instalador e o pacote portátil no Windows, gera `SHA256SUMS.txt` e publica os arquivos na release correspondente. Se qualquer verificação ou build falhar, a release não é publicada.

## Preparar uma versão

1. Escolha a próxima versão SemVer e atualize `version` em `desktop/package.json`.
2. A interface recebe a versão de `desktop/package.json`; não mantenha um segundo número manual.
3. Crie `docs/releases/vX.Y.Z.md` e `docs/en/releases/vX.Y.Z.md` com novidades, requisitos, limites e instruções relevantes.
4. Atualize o README e os documentos de arquitetura se eles citarem a versão anterior como a atual.
5. Revise os arquivos alterados, verifique que não há dados de biblioteca, tokens, logs ou backups e conclua os checks do CI no commit que será publicado.

O workflow compara a tag com `desktop/package.json`, verifica o número da interface e exige as notas da release. Uma divergência interrompe a publicação.

## Enviar commit e tag

Depois que o commit da versão estiver integrado à branch `main`, crie e envie uma tag anotada apontando para esse commit:

```powershell
git switch main
git pull --ff-only origin main
git tag -a vX.Y.Z -m "KOReader Ecosystem vX.Y.Z"
git push origin main
git push origin vX.Y.Z
```

Se a `main` já estiver no GitHub, não é necessário enviá-la novamente. A tag é o gatilho que inicia a publicação. Não reutilize uma tag de release existente; crie uma nova versão.

## Arquivos publicados

Cada release contém:

- `KOReaderEcosystem-Setup.exe` — instalador Windows x64.
- `KOReaderEcosystem-Windows-x64.zip` — pacote portátil com `KOReaderEcosystem.exe`, o serviço Python e as dependências necessárias. Extraia a pasta inteira; o executável não deve ser copiado isoladamente.
- `SHA256SUMS.txt` — hashes dos dois arquivos de distribuição.

A tag e o título da página da release exibem a versão; o instalador e o executável também carregam os metadados correspondentes. O link `/releases/latest` aponta para a última versão marcada como estável.

## Conferir a publicação

1. Aguarde a execução **Windows release** da tag terminar no GitHub Actions.
2. Confira se a release com a mesma tag contém exatamente o instalador, o ZIP e o arquivo de hashes.
3. Baixe os arquivos e valide os hashes localmente:

   ```powershell
   Get-FileHash .\KOReaderEcosystem-Setup.exe -Algorithm SHA256
   Get-FileHash .\KOReaderEcosystem-Windows-x64.zip -Algorithm SHA256
   ```

4. Confira que a instalação abre e que o pacote ZIP preserva o executável e seus arquivos de suporte.

O repositório permanece privado e o instalador ainda não tem assinatura digital. Não publique Setup, ZIP, `.nupkg`, executáveis avulsos, dados Calibre/Kindle, credenciais ou backups no histórico Git.

## Estado das releases

- `v0.1.2` foi publicada manualmente antes da automação e contém a prévia Electron somente leitura.
- `v0.2.0` é a primeira versão publicada pelo workflow automático de tag.
- `v0.2.6` inclui o histórico local de leitura KOReader e a verificação MTP híbrida; os assets são anexados quando o workflow de Windows termina.
- `v0.2.7` inclui cache/outbox Hardcover persistente, recuperação guiada do perfil local, edição de capa/campos personalizados compatíveis e tags em lote; os assets de Windows são publicados pelo workflow da tag.
- `v0.2.8` renomeia o pacote Python e o serviço do desktop, migra o perfil local e as credenciais antigas e atualiza a documentação pública.
- `v0.2.9` adiciona a interface em inglês/PT-BR, segue o idioma do Windows no primeiro uso e documenta os fluxos com capturas inglesas fictícias.
- `v0.3.0` adiciona intercâmbio CSV local compatível com Goodreads, StoryGraph e Maratona, com prévia revisável e exportação protegida.
- `v0.3.5` adiciona mapeamento revisável de cabeçalhos CSV em inglês e português antes da prévia local.
- `v0.3.4` adiciona revisão individual e local das ações ambíguas da outbox Hardcover sem expor texto de diário; `v0.3.3` aumenta para 48 px os alvos de ordenação e filtro e garante o uso de Inter nos controles; `v0.3.2` adiciona reconciliação manual e limitada da outbox, com repetição segura apenas para operações idempotentes.
- `v0.2.5` foi a release estável anterior com instalador, ZIP portátil e hashes SHA-256.
- `v0.2.4` é a release estável anterior e contém instalador, ZIP portátil e hashes SHA-256.
- `v0.2.3` tem uma tag sem Release publicada; a preparação foi substituída pela `v0.2.4` após falhas no CI.
- O workflow de release constrói somente a distribuição Electron. A distribuição PySide6 legada é produzida separadamente por `scripts/build-windows-app.ps1` e não acompanha os assets Electron.

O pacote Python, o módulo importável, o serviço de credenciais e o processo auxiliar do desktop usam `koreader-ecosystem` / `KOReaderEcosystem`. Na primeira execução após a atualização, a versão nova migra o perfil e copia as credenciais antigas para o novo identificador sem apagar as cópias anteriores. A versão do aplicativo Windows é controlada por `desktop/package.json`.
