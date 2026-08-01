# Ferramentas operacionais

O catálogo experimental da versão 1.27.0 organiza coletas por domínio e devolve saídas estruturadas para o planejador da IA.

## Domínios

- Checkmk e Livestatus;
- pfSense, dpinger, OpenVPN e IPsec;
- rede e captura limitada de cabeçalhos;
- Docker, Podman e sites OMD;
- hardware por Redfish.

## Restrições

- todas as ferramentas desta versão são somente leitura;
- integrações HTTP usam exclusivamente GET;
- argumentos são validados antes da execução;
- captura de pacotes exige filtro, dura no máximo 30 segundos, limita pacotes e não grava arquivo;
- credenciais permanecem no ambiente ou secret backend;
- as políticas de produção e standby continuam sendo aplicadas pelo executor.

## Validação real

O CI valida sintaxe, contratos, limites e ausência de métodos HTTP mutáveis. Endpoints Checkmk e caminhos Redfish ainda precisam ser confirmados em cada ambiente autorizado, pois URLs e identificadores variam conforme versão e fabricante.
