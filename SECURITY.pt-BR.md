# Política de segurança

[English](SECURITY.md) | [Português (Brasil)](SECURITY.pt-BR.md)

## Como relatar uma vulnerabilidade

Não relate vulnerabilidades em issues, discussões ou pull requests públicos.

Use o [relato privado de vulnerabilidades do GitHub](https://github.com/bellussi1/koreader-ecosystem/security/advisories/new) para contatar as pessoas mantenedoras em privado. Se o recurso estiver indisponível, entre em contato em privado pelo GitHub e solicite um canal seguro antes de compartilhar detalhes técnicos. O projeto ainda não publica um endereço dedicado para segurança nem um prazo de resposta.

Inclua uma descrição breve do componente afetado, impacto, versão ou commit afetado, uma reprodução usando dados sintéticos e possíveis formas de mitigação. Não envie uma prova de conceito que acesse dados de outra pessoa ou cause danos.

## Tratamento dos relatos

A manutenção deve reconhecer e avaliar os relatos em particular, verificar versões afetadas e impacto e coordenar a correção e divulgação com quem relatou, quando possível. Mantenha detalhes e reproduções privados até haver correção ou mitigação e a divulgação estar coordenada. Se houver credenciais expostas, revogue-as ou faça sua rotação prontamente. Após a correção, publique um aviso com versões afetadas, medidas de remediação e limitações remanescentes quando apropriado. Os prazos dependem da disponibilidade da manutenção e não são garantidos.

## Não inclua

Nunca envie conteúdo real da biblioteca, ebooks, bancos Calibre, sidecars `.sdr` do KOReader, snapshots de backup, exportações pessoais, tokens de acesso, chaves de API, senhas ou logs e capturas de tela com dados pessoais. Oculte caminhos locais, identificadores de conta ou dispositivo e informações sobre livros. Revogue qualquer credencial que tenha sido exposta.

## Escopo e versões suportadas

O aplicativo lida com dados locais do Calibre, arquivos do Kindle por USB/WPD/MTP e credenciais de serviços conectados. Relate problemas que possam expor ou alterar dados de usuários, contornar proteções de backup ou confirmação, executar conteúdo não confiável ou comprometer credenciais. Consulte as notas da versão mais recente disponível; ainda não há um cronograma formal de suporte publicado.
