from pathlib import Path
import os
import sys
import subprocess
import shutil
import ctypes
import concurrent.futures


# Pastas que normalmente não interessam e são pesadas para varrer.
# Pular essas pastas (sem nem entrar nelas) é o que mais acelera a busca.
PASTAS_IGNORADAS = {
    "windows",
    "programdata",
    "$recycle.bin",
    "system volume information",
    "node_modules",
    ".git",
    "appdata",
}


def limpar_terminal():
    comando = "cls" if os.name == "nt" else "clear"
    subprocess.run(comando, shell=True)


def listar_discos():
    """
    Retorna a lista de unidades de disco disponíveis.
    No Windows, detecta as letras de unidade (C:\\, D:\\, ...).
    Em outros sistemas, retorna apenas a raiz '/'.
    """
    discos = []

    if os.name == "nt":
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for letra in range(26):
            if bitmask & (1 << letra):
                discos.append(f"{chr(65 + letra)}:\\")
    else:
        discos.append("/")

    return discos


def exibir_discos():
    """
    Imprime na tela as unidades de disco disponíveis, com espaço
    livre e total de cada uma (quando possível ler essa informação).
    """
    discos = listar_discos()

    print("Discos disponíveis nesta máquina:")

    for disco in discos:
        try:
            uso = shutil.disk_usage(disco)
            total_gb = uso.total / (1024 ** 3)
            livre_gb = uso.free / (1024 ** 3)
            print(f"  {disco}   {livre_gb:.1f} GB livres de {total_gb:.1f} GB")
        except OSError:
            # Ex: leitor de CD/DVD ou cartão sem mídia inserida
            print(f"  {disco}   (não foi possível ler)")

    print()


def normalizar_raiz(raiz):
    """
    Corrige o caso clássico do Windows onde digitar apenas 'C:' (sem
    barra) NÃO significa a raiz do disco, e sim "o diretório atual
    daquela unidade" (que pode ser qualquer pasta). Isso faz a busca
    começar no lugar errado e "não encontrar" pastas/arquivos que na
    verdade existem, como C:\\Ryan\\Git.
    """
    raiz = raiz.strip().strip('"')

    # Ex: "C:" ou "c:" -> "C:\\"
    if len(raiz) == 2 and raiz[1] == ":":
        raiz += os.sep

    return raiz


def _varrer(diretorio, nome_alvo, tipo):
    """
    Faz a varredura de UM diretório e tudo abaixo dele. É a unidade de
    trabalho que roda em paralelo (uma thread por pasta de topo).
    """
    encontrados = []

    for dirpath, dirnames, filenames in os.walk(diretorio, onerror=lambda e: None):

        # Poda: remove pastas pesadas/irrelevantes da lista ANTES do
        # os.walk descer nelas. Isso evita varrer milhões de arquivos
        # do Windows/node_modules/etc, que é o maior ganho de velocidade.
        dirnames[:] = [d for d in dirnames if d.lower() not in PASTAS_IGNORADAS]

        alvo = dirnames if tipo == "pasta" else filenames

        for item in alvo:
            if item.lower() == nome_alvo:
                encontrados.append(os.path.join(dirpath, item))

    return encontrados


def buscar(nome, raiz, tipo, max_workers=8):
    """
    Busca 'nome' (pasta ou arquivo) a partir de 'raiz', varrendo as
    subpastas de primeiro nível EM PARALELO com várias threads.

    Como a busca em disco é limitada por I/O (esperar o disco
    responder, não pela CPU), rodar várias pastas ao mesmo tempo em
    threads aproveita esse tempo de espera e acelera bastante,
    especialmente em SSD.
    """
    nome_alvo = nome.lower()
    raiz_path = Path(raiz)
    resultados = []

    # Verifica os itens soltos direto na raiz (não entram no paralelismo)
    try:
        with os.scandir(raiz_path) as it:
            subpastas = []
            for entry in it:
                try:
                    if entry.is_dir():
                        if tipo == "pasta" and entry.name.lower() == nome_alvo:
                            resultados.append(entry.path)
                        if entry.name.lower() not in PASTAS_IGNORADAS:
                            subpastas.append(entry.path)
                    elif tipo == "arquivo" and entry.name.lower() == nome_alvo:
                        resultados.append(entry.path)
                except OSError:
                    continue
    except (PermissionError, OSError):
        subpastas = []

    # Cada subpasta de primeiro nível vira uma tarefa que roda em
    # paralelo (ex: C:\Users, C:\Program Files, C:\Ryan ao mesmo tempo)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futuros = [
            executor.submit(_varrer, sub, nome_alvo, tipo)
            for sub in subpastas
        ]

        for futuro in concurrent.futures.as_completed(futuros):
            resultados.extend(futuro.result())

    return resultados


# =========================================================
# LINKS CLICÁVEIS + ABRIR NO EXPLORADOR
# =========================================================

def caminho_para_link(caminho):
    """
    Formata o caminho como um hyperlink de terminal (padrão OSC 8),
    suportado por terminais modernos (Windows Terminal, VS Code,
    iTerm2, etc). Nesses terminais, dá pra clicar em cima do texto
    (geralmente Ctrl+clique) e ele abre o local no navegador/SO.

    Em terminais que não suportam OSC 8, o texto aparece normalmente,
    sem quebrar nada — por isso a opção de abrir pelo número (função
    abrir_no_explorador) continua sendo o caminho mais confiável.
    """
    uri = Path(caminho).resolve().as_uri()
    ESC = "\033"
    return f"{ESC}]8;;{uri}{ESC}\\{caminho}{ESC}]8;;{ESC}\\"


def abrir_no_explorador(caminho):
    """
    Abre o local no gerenciador de arquivos do sistema operacional.
    - Se for uma pasta: abre a própria pasta.
    - Se for um arquivo: abre a pasta que contém o arquivo e já
      deixa o arquivo selecionado (quando o SO suporta).
    """
    caminho = os.path.normpath(caminho)

    try:
        if os.name == "nt":
            if os.path.isdir(caminho):
                os.startfile(caminho)
            else:
                subprocess.run(["explorer", "/select,", caminho])
        elif sys.platform == "darwin":
            if os.path.isdir(caminho):
                subprocess.run(["open", caminho])
            else:
                subprocess.run(["open", "-R", caminho])
        else:
            alvo = caminho if os.path.isdir(caminho) else os.path.dirname(caminho)
            subprocess.run(["xdg-open", alvo])
        return True
    except OSError:
        return False


def listar_resultados(resultados, tipo):
    if len(resultados) == 1:
        print(f"\n{tipo} encontrado(a) em 1 local:")
    else:
        print(f"\n{tipo} encontrado(a) em {len(resultados)} locais:")

    for i, caminho in enumerate(resultados, start=1):
        print(f"  {i}. {caminho_para_link(caminho)}")

    print(
        "\n(Em terminais como Windows Terminal ou VS Code, "
        "Ctrl+clique no caminho abre o local direto.)"
    )


def oferecer_abertura(resultados):
    """
    Depois de listar os resultados, pergunta se o usuário quer abrir
    algum deles no explorador de arquivos, digitando o número.
    Funciona em qualquer terminal, mesmo os que não suportam links
    clicáveis.
    """
    if not resultados:
        return

    escolha = input(
        "\nDigite o número do item para abrir no explorador de "
        "arquivos (ou ENTER para pular): "
    ).strip()

    if not escolha:
        return

    if not escolha.isdigit() or not (1 <= int(escolha) <= len(resultados)):
        print("\nNúmero inválido.")
        return

    caminho_escolhido = resultados[int(escolha) - 1]

    if abrir_no_explorador(caminho_escolhido):
        print(f"\nAbrindo '{caminho_escolhido}'...")
    else:
        print(f"\nNão foi possível abrir '{caminho_escolhido}'.")


while True:

    limpar_terminal()

    print("==============================================")
    print("       SISTEMA DE ARQUIVOS E PASTAS")
    print("==============================================")
    print()
    print("1 - Procurar pasta em todo o diretório")
    print("2 - Procurar arquivo em todo o diretório")
    print("3 - Criar pasta")
    print("4 - Criar arquivo")
    print("5 - Listar discos")
    print("6 - Sair")
    print()

    opcao = input("Escolha uma opção: ").strip()

    # =========================
    # PROCURAR PASTA (em toda a raiz, ex: C:\)
    # =========================

    if opcao == "1":

        limpar_terminal()

        raiz = input(
            "\nDigite a unidade/diretório raiz onde a busca vai "
            "começar (ex: C:\\ ou C:\\Users): "
        ).strip()

        raiz = normalizar_raiz(raiz)

        raiz_path = Path(raiz)

        if not raiz_path.exists() or not raiz_path.is_dir():

            print("\nO diretório raiz informado não existe ou não é uma pasta.")

        else:

            nome_pasta = input(
                "Digite o nome exato da pasta que deseja procurar: "
            ).strip()

            print(f"\nBuscando '{nome_pasta}' em '{raiz}'... "
                  "isso pode demorar, dependendo do tamanho do disco.")

            resultados = buscar(nome_pasta, raiz, "pasta")

            if resultados:

                listar_resultados(resultados, "Pasta")

                if len(resultados) > 1:
                    print(
                        "\nAtenção: existe mais de uma pasta com esse "
                        "nome nos locais listados acima."
                    )

                oferecer_abertura(resultados)

            else:

                print(f"\nNenhuma pasta chamada '{nome_pasta}' foi "
                      "encontrada.")

                criar_pasta = input(
                    "Deseja criar essa pasta agora? (s/n): "
                ).strip().lower()

                if criar_pasta == "s":

                    destino = Path(
                        input(
                            "Em qual diretório deseja criar a pasta? "
                        ).strip()
                    )

                    if not destino.exists() or not destino.is_dir():

                        print("\nO diretório de destino informado não "
                              "é válido.")

                    else:

                        caminho_pasta = destino / nome_pasta
                        caminho_pasta.mkdir(parents=True, exist_ok=True)

                        print(
                            f"\nPasta '{nome_pasta}' criada com sucesso "
                            f"em '{destino}'."
                        )

        input("\nPressione ENTER para continuar...")
        limpar_terminal()

    # =========================
    # PROCURAR ARQUIVO (em toda a raiz, ex: C:\)
    # =========================

    elif opcao == "2":

        limpar_terminal()

        raiz = input(
            "\nDigite a unidade/diretório raiz onde a busca vai "
            "começar (ex: C:\\ ou C:\\Users): "
        ).strip()

        raiz = normalizar_raiz(raiz)

        raiz_path = Path(raiz)

        if not raiz_path.exists() or not raiz_path.is_dir():

            print("\nO diretório raiz informado não existe ou não é uma pasta.")

        else:

            nome_arquivo = input(
                "Digite o nome exato do arquivo (ex: arquivo.txt): "
            ).strip()

            print(f"\nBuscando '{nome_arquivo}' em '{raiz}'... "
                  "isso pode demorar, dependendo do tamanho do disco.")

            resultados = buscar(nome_arquivo, raiz, "arquivo")

            if resultados:

                listar_resultados(resultados, "Arquivo")

                if len(resultados) > 1:
                    print(
                        "\nAtenção: existe mais de um arquivo com esse "
                        "nome nos locais listados acima."
                    )

                oferecer_abertura(resultados)

            else:

                print(f"\nNenhum arquivo chamado '{nome_arquivo}' foi "
                      "encontrado.")

                criar_arquivo = input(
                    "Deseja criar esse arquivo agora? (s/n): "
                ).strip().lower()

                if criar_arquivo == "s":

                    destino = Path(
                        input(
                            "Em qual diretório deseja criar o arquivo? "
                        ).strip()
                    )

                    if not destino.exists() or not destino.is_dir():

                        print("\nO diretório de destino informado não "
                              "é válido.")

                    else:

                        caminho_arquivo = destino / nome_arquivo
                        caminho_arquivo.touch(exist_ok=True)

                        print(
                            f"\nArquivo '{nome_arquivo}' criado com "
                            f"sucesso em '{destino}'."
                        )

        input("\nPressione ENTER para continuar...")
        limpar_terminal()

    # =========================
    # CRIAR PASTA (direto, sem busca prévia)
    # =========================

    elif opcao == "3":

        limpar_terminal()

        diretorio = Path(
            input(
                "\nDigite o diretório onde deseja criar a pasta: "
            ).strip()
        )

        if not diretorio.exists() or not diretorio.is_dir():

            print("\nO diretório informado não existe ou não é uma pasta.")

        else:

            pasta = input("Digite o nome da pasta a ser criada: ").strip()
            caminho_pasta = diretorio / pasta

            if caminho_pasta.exists():

                print(f"\nJá existe algo chamado '{pasta}' nesse diretório.")

            else:

                caminho_pasta.mkdir(parents=True, exist_ok=True)
                print(
                    f"\nPasta '{pasta}' criada com sucesso em "
                    f"'{diretorio}'."
                )

        input("\nPressione ENTER para continuar...")
        limpar_terminal()

    # =========================
    # CRIAR ARQUIVO (direto, sem busca prévia)
    # =========================

    elif opcao == "4":

        limpar_terminal()

        diretorio = Path(
            input(
                "\nDigite o diretório onde deseja criar o arquivo: "
            ).strip()
        )

        if not diretorio.exists() or not diretorio.is_dir():

            print("\nO diretório informado não existe ou não é uma pasta.")

        else:

            arquivo = input(
                "Digite o nome do arquivo a ser criado (ex: arquivo.txt): "
            ).strip()
            caminho_arquivo = diretorio / arquivo

            if caminho_arquivo.exists():

                print(f"\nJá existe algo chamado '{arquivo}' nesse diretório.")

            else:

                caminho_arquivo.touch(exist_ok=True)
                print(
                    f"\nArquivo '{arquivo}' criado com sucesso em "
                    f"'{diretorio}'."
                )

        input("\nPressione ENTER para continuar...")
        limpar_terminal()

    # =========================
    # LISTAR DISCOS
    # =========================

    elif opcao == "5":

        limpar_terminal()

        exibir_discos()

        input("Pressione ENTER para continuar...")
        limpar_terminal()

    # =========================
    # SAIR
    # =========================

    elif opcao == "6":

        limpar_terminal()

        print("==============================================")
        print("       Programa encerrado.")
        print("==============================================")

        break

    # =========================
    # OPÇÃO INVÁLIDA
    # =========================

    else:

        print("\nOpção inválida.")

        input("\nPressione ENTER para continuar...")
        limpar_terminal()
