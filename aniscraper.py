import asyncio
import json
import re
import warnings
import os
from dataclasses import dataclass, field, asdict
from typing import List, Optional
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# Silencia avisos de parser para manter o console limpo
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

@dataclass
class Video:
    quality: str
    url: str

@dataclass
class Episode:
    number: str
    url: str
    thumb: str = "" 
    videos: List[Video] = field(default_factory=list)

@dataclass
class AnimeData:
    title: str
    slug: str
    url: str
    thumbnail: str 
    description: str
    genres: List[str]
    status: str
    year: str
    episodes: List[Episode] = field(default_factory=list)

class AnimeFireAPI:
    def __init__(self):
        self.base_url = "https://animefire.io"
        # Impersonate simula um navegador real para evitar bloqueios
        self.session = AsyncSession(impersonate="chrome110")
        self.session.headers.update({"Referer": self.base_url})
        # Limita a 15 requisições simultâneas para não ser banido pelo servidor
        self.semaphore = asyncio.Semaphore(15) 

    def _get_info_text(self, soup, label):
        """Extrai metadados baseados no rótulo dentro de tags <b>."""
        for info in soup.select(".animeInfo"):
            b_tag = info.select_one("b")
            if b_tag and label.lower() in b_tag.text.lower():
                span = info.select_one("span")
                return span.text.strip() if span else "N/A"
        return "N/A"

    def _clean_slug_for_assets(self, slug: str) -> str:
        """Remove o sufixo '-dublado' para carregar capas e thumbs corretamente."""
        return slug.replace("-dublado", "")

    async def get_all_links_from_sitemap(self) -> List[str]:
        """Busca todos os links de animes no sitemap do site."""
        print("📥 Acessando sitemap para buscar lista de animes...")
        try:
            resp = await self.session.get(f"{self.base_url}/sitemap.xml", timeout=30)
            # No GitHub Actions, o html.parser é mais estável que o xml puro
            soup = BeautifulSoup(resp.text, "html.parser") 
            links = [loc.text for loc in soup.find_all("loc") if "-todos-os-episodios" in loc.text]
            
            print(f"🔗 Links encontrados no sitemap: {len(links)}")
            return sorted(list(set(links)))
        except Exception as e:
            print(f"❌ Erro crítico ao baixar sitemap: {e}")
            return []

    async def get_video_links(self, ep_name: str, ep_url: str, anime_slug: str) -> Episode:
        """Extrai links de vídeo de um episódio específico."""
        async with self.semaphore:
            try:
                ep_number = ep_url.split("/")[-1]
                asset_slug = self._clean_slug_for_assets(anime_slug)
                ep_thumb = f"https://animefire.io/img/video/{asset_slug}/{ep_number}.webp"

                resp = await self.session.get(ep_url, timeout=15)
                soup = BeautifulSoup(resp.text, "html.parser")
                videos = []

                # Tenta extrair do player principal (API interna)
                video_tag = soup.select_one("video#my-video")
                if video_tag and video_tag.get("data-video-src"):
                    api_resp = await self.session.get(video_tag["data-video-src"])
                    for item in api_resp.json().get("data", []):
                        video_url = item.get("src", "")
                        # Remove vídeos hospedados no Blogger (baixa qualidade/instáveis)
                        if "source=blogger" not in video_url:
                            videos.append(Video(quality=item.get("label"), url=video_url))

                # Se não achou no player principal, tenta no iframe (fallback)
                if not videos:
                    iframe = soup.select_one("div#div_video iframe")
                    if iframe:
                        iframe_resp = await self.session.get(iframe.get("src"))
                        m = re.search(r'play_url"\s*:\s*"([^"]+)', iframe_resp.text)
                        if m:
                            video_url = m.group(1)
                            if "source=blogger" not in video_url:
                                videos.append(Video(quality="SD/HD", url=video_url))
                
                return Episode(number=ep_name, url=ep_url, thumb=ep_thumb, videos=videos)
            except Exception:
                return Episode(number=ep_name, url=ep_url, thumb="", videos=[])

    async def scrape_full_anime(self, url: str) -> Optional[AnimeData]:
        """Coleta todos os dados de um anime e seus respectivos episódios."""
        async with self.semaphore:
            try:
                resp = await self.session.get(url, timeout=20)
                soup = BeautifulSoup(resp.text, "html.parser")
                
                title_tag = soup.find("h1")
                if not title_tag: return None
                
                anime_slug = url.split("/")[-1].replace("-todos-os-episodios", "")
                asset_slug = self._clean_slug_for_assets(anime_slug)
                cover_url = f"https://animefire.io/img/animes/{asset_slug}-large.webp"

                anime = AnimeData(
                    title=title_tag.text.strip(),
                    slug=anime_slug,
                    url=url,
                    thumbnail=cover_url,
                    description=soup.select_one("div.divSinopse").text.strip() if soup.select_one("div.divSinopse") else "",
                    genres=[a.text.strip() for a in soup.select("a.spanGeneros")],
                    status=self._get_info_text(soup, "Status"),
                    year=self._get_info_text(soup, "Ano")
                )

                ep_elements = soup.select("div.div_video_list > a")
                if ep_elements:
                    tasks = [self.get_video_links(a.text.strip(), a["href"], anime_slug) for a in ep_elements]
                    episodes_results = await asyncio.gather(*tasks)
                    # Filtro: Só mantém episódios que possuem algum vídeo válido
                    anime.episodes = [ep for ep in episodes_results if len(ep.videos) > 0]

                return anime
            except Exception as e:
                print(f"⚠️ Erro ao processar anime {url}: {e}")
                return None

    async def run_automation(self):
        """Lógica principal de scraping incremental e atualização."""
        links = await self.get_all_links_from_sitemap()
        
        if not links:
            print("🛑 Erro: Sitemap vazio ou bloqueado. Abortando para proteger a base de dados.")
            return

        db_path = "animes_full_db.json"
        db_map = {}

        # Tenta carregar dados existentes
        if os.path.exists(db_path):
            try:
                with open(db_path, "r", encoding="utf-8") as f:
                    data_list = json.load(f)
                    # Usamos dicionário para evitar duplicatas e facilitar a atualização
                    db_map = {anime['slug']: anime for anime in data_list}
                print(f"📦 Database carregada: {len(db_map)} animes encontrados.")
            except json.JSONDecodeError:
                print("🆕 Database corrompida ou vazia. Iniciando do zero.")
        else:
            print("🆕 Criando nova base de dados.")

        # Filtra quais links precisam de processamento
        links_to_process = []
        for l in links:
            slug = l.split("/")[-1].replace("-todos-os-episodios", "")
            
            # Condição: Anime novo OU anime que ainda está saindo episódios
            if slug not in db_map:
                links_to_process.append(l)
            elif db_map[slug].get("status") == "Em lançamento":
                links_to_process.append(l)

        if not links_to_process:
            print("✅ O banco de dados já está totalmente atualizado.")
            return

        print(f"🚀 Iniciando extração de {len(links_to_process)} animes (Novos/Lançamentos)...")

        # Processamento em lotes (batch) para não sobrecarregar
        batch_size = 10 
        for i in range(0, len(links_to_process), batch_size):
            batch = links_to_process[i : i + batch_size]
            tasks = [self.scrape_full_anime(link) for link in batch]
            results = await asyncio.gather(*tasks)
            
            for r in results:
                if r:
                    # Sobrescreve ou adiciona no mapa (mantém sempre a versão mais nova)
                    db_map[r.slug] = asdict(r)
            
            # Salva o progresso a cada lote
            with open(db_path, "w", encoding="utf-8") as f:
                json.dump(list(db_map.values()), f, indent=4, ensure_ascii=False)
            
            print(f"⏳ Progresso: {len(db_map)} / {len(links)} animes processados.")
