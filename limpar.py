import json

def processar_lista_animes(caminho_entrada, caminho_saida):
    try:
        # Carrega a lista imensa
        with open(caminho_entrada, 'r', encoding='utf-8') as f:
            lista_animes = json.load(f)
        
        print(f"Total de animes carregados: {len(lista_animes)}")
        
        animes_limpos = []

        for anime in lista_animes:
            if "episodes" in anime:
                for episodio in anime["episodes"]:
                    # Filtra a lista de vídeos de cada episódio
                    videos_filtrados = [
                        v for v in episodio.get("videos", []) 
                        if "source=blogger" not in v.get("url", "")
                    ]
                    # Atualiza a lista de vídeos do episódio
                    episodio["videos"] = videos_filtrados
            
            animes_limpos.append(anime)

        # Salva o resultado
        with open(caminho_saida, 'w', encoding='utf-8') as f:
            json.dump(animes_limpos, f, indent=4, ensure_ascii=False)
        
        print(f"Sucesso! O arquivo limpo foi salvo em: {caminho_saida}")

    except Exception as e:
        print(f"Ocorreu um erro: {e}")

# Execução
processar_lista_animes('animes.json', 'lista_limpa.json')