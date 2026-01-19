import json
import os

def organizar_database(arquivo_origem):
    # Carrega o JSON principal
    with open(arquivo_origem, 'r', encoding='utf-8') as f:
        animes = json.load(f)

    # Cria a pasta de saída se não existir
    output_dir = 'api_data'
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Dicionários para organizar os dados
    data = {
        "lancamentos": [],
        "dublados": [],
        "generos": {}
    }

    for anime in animes:
        # 1. Filtra Lançamentos
        if anime.get("status") == "Em lançamento":
            data["lancamentos"].append(anime)

        # 2. Filtra Dublados (pelo Slug ou Título)
        if "dublado" in anime.get("slug", "").lower():
            data["dublados"].append(anime)

        # 3. Organiza por Gênero
        for genero in anime.get("genres", []):
            gen_key = genero.lower().replace(" ", "_")
            if gen_key not in data["generos"]:
                data["generos"][gen_key] = []
            data["generos"][gen_key].append(anime)

    # Salva arquivos individuais (Isso economiza banda do seu App)
    
    # Salva Lançamentos
    with open(f'{output_dir}/lancamentos.json', 'w', encoding='utf-8') as f:
        json.dump(data["lancamentos"], f, indent=4, ensure_ascii=False)

    # Salva Dublados
    with open(f'{output_dir}/dublados.json', 'w', encoding='utf-8') as f:
        json.dump(data["dublados"], f, indent=4, ensure_ascii=False)

    # Salva um JSON para cada gênero (Ex: api_data/genero_horror.json)
    for gen, lista in data["generos"].items():
        with open(f'{output_dir}/genero_{gen}.json', 'w', encoding='utf-8') as f:
            json.dump(lista, f, indent=4, ensure_ascii=False)

    print(f"Processamento concluído! Arquivos gerados na pasta '{output_dir}'.")

# Executar
#organizar_database('lista_limpa.json')
