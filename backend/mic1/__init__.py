"""Núcleo do Simulador MIC-1 (Tanenbaum, Organização Estruturada de Computadores, 3ª ed.).

Máquina de 16 bits com a ISA MAC-1 (23 macroinstruções), microinstrução de 32 bits e
memória de controle de 256 × 32.

Módulos:

* ``bits``: aritmética de 16 bits e endereços de 12 bits;
* ``datapath``: 16 registradores, latches, AMUX, ULA, deslocador, MAR e MBR;
* ``control``: MIR, memória de controle, MPC/MMUX e os dois modos de decodificação;
* ``memory``: memória principal de 4096 palavras com leitura/escrita de 6 subciclos;
* ``ports``: separa os acessos em instrução ou dado e manda cada um para a sua cache;
* ``isa``: codificação e decodificação das instruções MAC-1;
* ``cache``: I-Cache e D-Cache (mapeamento direto ou 2 vias com LRU, write-through);
* ``assembler``: montador do microprograma (MAL) e montador MAC-1;
* ``cpu``: junta tudo e executa por subciclo, microinstrução, macroinstrução ou até o HALT.

O pacote é Python puro e não importa nada de ``server/`` nem de bibliotecas web
(``tests/test_architecture.py`` confere isso).
"""

__version__ = "1.0.0"
