# PGE .quant — Storytelling para Apresentação

---

## INTRODUÇÃO

### Tema
**Jurimetria aplicada à gestão do contencioso público:** o uso de análise quantitativa de dados processuais como instrumento de inteligência estratégica na Procuradoria Geral do Estado de São Paulo — Núcleo de Pessoal Militar (NPM/PGE-SP).

---

### O Problema — O que acontece quando o Estado litiga no escuro?

O Núcleo de Pessoal Militar da PGE/SP defende o Estado de São Paulo em ações movidas por servidores da Polícia Militar. O volume é expressivo: **mais de 1.700 processos cadastrados apenas em maio de 2026**, com **quase 38.000 demandas processuais** no mesmo mês, distribuídas entre **41 procuradores** e abrangendo **83 teses jurídicas distintas**.

Mas o que acontecia antes com esses dados?

Eles existiam. Estavam no sistema de gestão processual, exportáveis em planilha, cheios de informação. E ficavam lá — inativos, sem transformação, sem análise, sem uso estratégico.

O resultado era uma gestão no escuro:
- **Não se sabia quais teses estavam ganhando e quais estavam perdendo** com precisão estatística.
- **Não havia visibilidade sobre a exposição financeira real** do Estado — qual o valor em risco nos processos em andamento.
- **A produtividade do núcleo era gerenciada por percepção**, não por dado — quantas demandas cada procurador processou? Quantas horas? Qual o ritmo de conclusão?
- **Padrões e tendências temporais eram invisíveis** — os ajuizamentos estão crescendo? Existe sazonalidade? Qual tese está ganhando volume?
- **Gargalos operacionais não eram identificados proativamente** — quais processos ficavam parados sem demanda há mais tempo?

Em síntese: havia abundância de dados e escassez de informação.

---

### Delimitação do Problema

O problema não é a falta de dados processuais — o sistema de gestão já os registra. O problema é a **ausência de uma camada analítica** que transforme esses dados brutos em inteligência operacional e estratégica, acessível, visual e acionável para procuradores e coordenadores do NPM.

A delimitação é dupla:
1. **Dimensão financeira:** mensurar com precisão a exposição do Estado — valor em risco, economia obtida por êxito processual, distribuição de valor por tese.
2. **Dimensão operacional:** medir a produtividade do núcleo — fluxo de demandas, carga por procurador, sazonalidade, gargalos.

---

### Justificativa — Por que isso importa?

**1. Escala e impacto financeiro.**
O NPM defende interesses do Estado em um volume contínuo de processos cujo somatório de pedidos representa exposição financeira relevante ao erário. Sem medir essa exposição por tese, por comarca e por período, é impossível priorizar estratégias de defesa.

**2. A diferença entre reagir e antecipar.**
A gestão processual tradicional é reativa: o procurador recebe a intimação e responde. A jurimetria permite postura proativa — identificar que determinada tese tem taxa de êxito baixa e acionar revisão de estratégia antes que o passivo se consolide.

**3. O potencial de precedentes.**
No contencioso massificado do pessoal militar, os processos não são ilhas isoladas — eles orbitam em torno de teses comuns (DEJEM, licença-prêmio, LC 173/20, bonificação de resultado). Um dado sobre a taxa de êxito nessas teses é, na prática, um dado sobre centenas de processos análogos.

**4. Prestação de contas e eficiência.**
A Administração Pública tem obrigação de eficiência (art. 37, CF/88). Instrumentos de medição de produtividade e resultado são não apenas úteis — são necessários para a gestão responsável de um núcleo com dezenas de procuradores e dezenas de milhares de demandas mensais.

**5. Inovação acessível.**
A construção desse sistema foi feita com tecnologia de código aberto (Python, Streamlit, SQLite, Plotly), sem custo de licença e sem dependência de grandes sistemas de TI. Isso o torna replicável para qualquer núcleo da PGE/SP.

---

### Objetivos

**Objetivo Geral:**
Desenvolver e implementar uma plataforma de jurimetria — o **PGE .quant** — que transforme os relatórios de exportação do sistema de gestão processual do NPM em inteligência quantitativa visual e acionável.

**Objetivos Específicos:**
1. Construir uma pipeline de ingestão de dados que consolide os relatórios mensais de processos e demandas em uma base histórica persistente.
2. Desenvolver painéis analíticos interativos para duas dimensões distintas: **Frente 1 — Análise Financeira e Resultados** e **Frente 2 — Gestão Operacional e Produtividade**.
3. Implementar filtros dinâmicos por núcleo, procurador, matéria, tese e período para segmentação granular das análises.
4. Gerar relatórios Word institucionais automáticos com os dados filtrados, exportáveis em um clique.
5. Tornar o sistema acessível como aplicação local, sem necessidade de conexão à internet ou infraestrutura de TI adicional.

---

### Metodologia (visão geral)

O projeto foi desenvolvido em três camadas:

**Camada 1 — Dados:** os relatórios exportados do sistema de gestão são importados em formato `.txt` (CSV com vírgula). Um motor de ingestão os processa, unifica processos duplicados (mesmo processo com múltiplos valores de dívida), deriva campos calculados e persiste tudo em um banco SQLite local.

**Camada 2 — Análise:** a engine analítica em Python (Pandas + lógica proprietária) calcula KPIs, classifica estágios processuais, identifica padrões de êxito por tese e comarca, e mede produtividade por procurador.

**Camada 3 — Interface:** o Streamlit renderiza os dashboards interativos com gráficos Plotly. A geração de relatórios Word usa templates `.docx` com placeholders substituídos dinamicamente pelos dados filtrados.

---
---

## DESENVOLVIMENTO

---

### Fundamentação — O que é Jurimetria e por que ela chega à PGE/SP?

**Jurimetria** é a aplicação do método estatístico e quantitativo ao fenômeno jurídico. O termo, cunhado pelo jurista americano Lee Loevinger nos anos 1960, descreve o estudo empírico do direito: ao invés de raciocinar sobre normas em abstrato, raciocinar sobre o que efetivamente acontece nos processos — quem ganha, quem perde, quanto tempo demora, qual o valor, qual o padrão.

No Brasil, a jurimetria ganhou tração nas últimas décadas, especialmente com o CNJ e entidades como a ABJ (Associação Brasileira de Jurimetria), que passou a produzir diagnósticos quantitativos do Judiciário. O setor privado — grandes escritórios e empresas com alto volume de litígio — já incorporou ferramentas jurimétricas na sua gestão.

O contencioso público, no entanto, ficou para trás. A Advocacia Pública ainda opera majoritariamente com gestão qualitativa e intuitiva dos acervos. O PGE .quant nasce para mudar isso — pelo menos dentro do NPM.

**Por que o pessoal militar é o contexto ideal?**

O contencioso do pessoal militar tem características que o tornam especialmente adequado à análise jurimétrica:
- **Volume elevado e contínuo:** centenas de novos processos por mês.
- **Alta repetitividade de teses:** as mesmas matérias (DEJEM, licença-prêmio, LC 173/20, bonificação de resultado) se repetem em centenas de processos análogos.
- **Estratificação clara de procuradores e mesas:** cada processo é atribuído a um procurador e a uma mesa, permitindo análise de produtividade.
- **Dado processual estruturado:** o sistema de gestão já registra os campos relevantes — valor, data, tese, comarca, parte contrária, resultado.

---

### Metodologia — Como o PGE .quant foi construído

**Etapa 1 — Arquitetura de dados**

O sistema recebe dois tipos de arquivo exportado mensalmente:

| Arquivo | Conteúdo | Campos principais |
|---|---|---|
| Relatório de Processos | Um registro por processo (ou por dívida) | Processo, valor, ajuizamento, tese, comarca, polo, resultado |
| Relatório de Demandas | Um registro por demanda/providência | Procurador, tipo de demanda, entrada, conclusão, horas, status |

O motor de ingestão executa:
- Limpeza e normalização de campos (datas, valores monetários, nomes de teses)
- Consolidação por processo único (múltiplos valores de dívida são somados)
- Derivação de campos calculados: `status_exito` (Vitória / Perda / Em Andamento), `comarca_limpa`, `assunto_label`
- Persistência em SQLite com controle de versionamento (uploads não destroem dados históricos)

**Etapa 2 — Frente 1: Análise Financeira e Resultados**

Foco: **o acervo de processos**. Responde às perguntas estratégicas:

- Qual a nossa exposição financeira atual? (valor em risco nos processos em andamento)
- Em quais teses estamos ganhando? Em quais estamos perdendo?
- Qual a taxa de êxito por tese, por comarca, por período?
- Onde está geograficamente concentrado o nosso contencioso?
- O acervo está crescendo ou reduzindo ao longo do tempo?

Painéis implementados: Panorama Financeiro (KPIs), Êxito por Tese, Estágio e Instâncias, Mapeamento Geográfico (choropleth do Estado de SP), Linha do Tempo, Detalhamento individual.

**Etapa 3 — Frente 2: Gestão Operacional e Produtividade**

Foco: **o fluxo de trabalho do núcleo**. Responde às perguntas operacionais:

- Quantas demandas o núcleo processou no período?
- Qual a distribuição de carga por procurador?
- O fluxo de entradas está equilibrado com o de conclusões?
- Existe sazonalidade operacional — meses mais carregados?
- Quais demandas estão há mais tempo em aberto sem conclusão?

Painéis implementados: Performance dos Núcleos (KPIs), Produtividade por Procurador, Fluxo de Demandas (entradas × conclusões × saldo), Sazonalidade Operacional, Gargalos de Pendência, Histórico e Linha do Tempo.

**Etapa 4 — Geração de Relatórios**

Dois botões no dashboard geram relatórios Word institucionais com os dados do estado de filtros vigente. Os templates `.docx` têm identidade visual da PGE/SP e estrutura pré-definida. O Python substitui os placeholders pelos valores calculados e entrega o arquivo para download imediato.

**Stack tecnológica:**
- **Python 3.x** — linguagem principal
- **Pandas** — manipulação e análise de dados
- **Streamlit** — interface web interativa
- **Plotly** — visualizações interativas
- **SQLite** — banco de dados local persistente
- **python-docx** — geração dos relatórios Word
- **Custo de infraestrutura:** zero (roda localmente, sem servidores)

---

### Resultados — O que o PGE .quant entrega

**Resultado 1 — Visibilidade do acervo em números reais**

Com os dados de maio de 2026 do NPM, o sistema revelou:
- **1.728 processos únicos** no acervo ativo do mês
- **83 teses jurídicas distintas** em disputa
- Teses com maior volume: Diária Especial por Jornada Extraordinária de PM (DEJEM), contagem de tempo/licença-prêmio (LC 173/20), e licença-prêmio em pecúnia indenizada — as três sozinhas respondem pela maioria dos processos
- Exposição financeira mensurável por tese e por comarca — antes invisível

**Resultado 2 — Inteligência operacional do núcleo**

- **37.963 demandas** processadas em maio de 2026 por 41 procuradores
- **Taxa de conclusão de 98,4%** no mês (37.349 concluídas, 614 em aberto)
- **135 tipos de demanda distintos** mapeados — de citações e intimações a RPVs e requerimentos exclusivos da PMESP
- Ranking de produtividade por procurador disponível em tempo real, com filtro por período

**Resultado 3 — Relatórios institucionais automáticos**

Em vez de horas de trabalho manual para montar um relatório mensal, o gestor filtra o período e o núcleo no dashboard e clica em "Gerar Relatório". Em segundos, recebe um `.docx` formatado com logo, KPIs, tabelas de teses, comarcas, procuradores e linha do tempo — pronto para envio ou arquivamento.

**Resultado 4 — Base histórica acumulativa**

Cada upload mensal enriquece a base. Com o tempo, o sistema passa a responder perguntas que nenhum relatório mensal isolado consegue: a taxa de êxito na tese X melhorou após a mudança de estratégia em Y? O acervo está crescendo ou se estabilizando? Qual procurador mais cresceu em produtividade?

---
---

## CONCLUSÃO

### O que foi construído

O PGE .quant é a primeira iniciativa de jurimetria sistematizada no âmbito do Núcleo de Pessoal Militar da PGE/SP. Ele transforma dados que já existiam — exportados mensalmente do sistema de gestão, mas subutilizados — em inteligência estratégica e operacional de acesso imediato.

Não é uma substituição da análise jurídica qualitativa feita pelos procuradores. É o complemento quantitativo que ela nunca teve: o dado que confirma ou desafia a intuição, que revela o padrão que a experiência individual não alcança, que mede o que antes só era estimado.

### O que isso significa para a gestão pública

A Advocacia Pública gerencia riscos financeiros bilionários para o Estado. A diferença entre um núcleo que sabe exatamente onde está ganhando e onde está perdendo — e por quê — e um núcleo que opera por intuição, pode se traduzir em dezenas de milhões de reais em passivos evitáveis ou economias realizadas.

O PGE .quant demonstra que a modernização da gestão jurídica pública não exige grandes contratos de tecnologia, não depende de orçamento extraordinário e não precisa esperar por sistemas corporativos. Pode nascer internamente, com ferramentas abertas, por quem conhece o dado e o problema.

### Próximos passos

1. **Expansão para outros núcleos da PGE/SP** — o sistema foi projetado como multi-núcleo desde o início. A estrutura de dados e os painéis são adaptáveis para NPCE, NPSS, e outros.
2. **Análise preditiva** — com histórico suficiente, modelos de machine learning podem estimar a probabilidade de êxito por tese e perfil de processo.
3. **Integração direta com o sistema de gestão** — eliminar a etapa de exportação manual e ingestão via upload, passando a consumir a API do sistema diretamente.
4. **Painel de precedentes** — cruzamento com jurisprudência do TJSP e STJ para contextualizar os resultados do NPM no panorama judicial mais amplo.

### A mensagem final

Dados processuais são o ativo mais subutilizado da Advocacia Pública brasileira. O PGE .quant é uma resposta concreta, funcional e imediatamente disponível para esse problema — construída de dentro para fora, por quem conhece o contencioso e entende o que a gestão precisa ver.

**Mais do que um programa, é uma mudança de postura: da gestão por intuição para a gestão por evidência.**

---
*PGE .quant — Análise Quantitativa do Contencioso | NPM/PGE-SP | 2026*
