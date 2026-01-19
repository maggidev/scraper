import asyncio
import json
import re
import warnings
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
        self.session = AsyncSession(impersonate="chrome110")
        self.session.headers.update({"Referer": self.base_url})
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
        print("📥 Acessando sitemap para buscar lista de animes...")
        try:
            resp = await self.session.get(f"{self.base_url}/sitemap.xml")
            soup = BeautifulSoup(resp.text, features="xml")
            links = [loc.text for loc in soup.find_all("loc") if "-todos-os-episodios" in loc.text]
            return sorted(list(set(links)))
        except Exception as e:
            print(f"❌ Erro ao baixar sitemap: {e}")
            return []

    # No aniscraper.py, altere a função get_video_links:

async def get_video_links(self, ep_name: str, ep_url: str, anime_slug: str) -> Episode:
    """Extrai links de vídeo e gera thumbnail corrigida, filtrando links blogger."""
    async with self.semaphore:
        try:
            ep_number = ep_url.split("/")[-1]
            asset_slug = self._clean_slug_for_assets(anime_slug)
            ep_thumb = f"https://animefire.io/img/video/{asset_slug}/{ep_number}.webp"

            resp = await self.session.get(ep_url, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")
            videos = []

            video_tag = soup.select_one("video#my-video")
            if video_tag and video_tag.get("data-video-src"):
                api_resp = await self.session.get(video_tag["data-video-src"])
                for item in api_resp.json().get("data", []):
                    # --- FILTRO AQUI ---
                    video_url = item.get("src", "")
                    if "source=blogger" not in video_url:
                        videos.append(Video(quality=item.get("label"), url=video_url))

            if not videos:
                iframe = soup.select_one("div#div_video iframe")
                if iframe:
                    iframe_resp = await self.session.get(iframe.get("src"))
                    m = re.search(r'play_url"\s*:\s*"([^"]+)', iframe_resp.text)
                    if m:
                        # --- FILTRO AQUI TAMBÉM ---
                        video_url = m.group(1)
                        if "source=blogger" not in video_url:
                            videos.append(Video(quality="SD/HD", url=video_url))
            
            return Episode(number=ep_name, url=ep_url, thumb=ep_thumb, videos=videos)
        except Exception:
            return Episode(number=ep_name, url=ep_url, thumb="", videos=[])

    async def scrape_full_anime(self, url: str) -> Optional[AnimeData]:
        """Faz o scrape completo da página do anime com correção de assets."""
        async with self.semaphore:
            try:
                resp = await self.session.get(url, timeout=20)
                soup = BeautifulSoup(resp.text, "html.parser")
                
                title_tag = soup.find("h1")
                if not title_tag: return None
                
                # Slug real do anime para links e identificação
                anime_slug = url.split("/")[-1].replace("-todos-os-episodios", "")
                
                # Slug limpo para buscar a imagem no servidor de assets
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
                    anime.episodes = await asyncio.gather(*tasks)

                return anime
            except Exception as e:
                print(f"⚠️ Erro ao processar anime {url}: {e}")
                return None

    async def run_automation(self):
        links = await self.get_all_links_from_sitemap()
        total_sitemap = len(links)
        
        all_data = []
        processed_slugs = set()
        
        try:
            with open("animes_full_db.json", "r", encoding="utf-8") as f:
                all_data = json.load(f)
                processed_slugs = {anime['slug'] for anime in all_data}
                print(f"📦 Checkpoint: {len(processed_slugs)} animes já carregados.")
        except (FileNotFoundError, json.JSONDecodeError):
            print("🆕 Criando novo banco de dados...")

        links_to_process = [
            l for l in links 
            if l.split("/")[-1].replace("-todos-os-episodios", "") not in processed_slugs
        ]
        
        if not links_to_process:
            print("✅ O banco de dados já está atualizado.")
            return

        print(f"🚀 Extraindo {len(links_to_process)} novos itens...")

        batch_size = 10 
        for i in range(0, len(links_to_process), batch_size):
            batch = links_to_process[i : i + batch_size]
            tasks = [self.scrape_full_anime(link) for link in batch]
            results = await asyncio.gather(*tasks)
            
            valid_results = [asdict(r) for r in results if r]
            all_data.extend(valid_results)
            
            with open("animes_full_db.json", "w", encoding="utf-8") as f:
                json.dump(all_data, f, indent=4, ensure_ascii=False)
            
            print(f"⏳ Salvo: {len(all_data)} / {total_sitemap} animes totais.")

async def main():
    api = AnimeFireAPI()
    await api.run_automation()

if __name__ == "__main__":
    asyncio.run(main())