"""Generate the offline benchmark: a small *synthetic web* about a FICTIONAL grid-scale sodium-ion
battery market, plus research questions with required facts and trap facts.

Everything here (companies, figures, outlets' articles) is invented for testing. The pages are
designed to stress the parts of a research agent that matter:
  * a content-farm listicle with an inflated market size        -> credibility weighting
  * a stale 2019 page with outdated costs                       -> recency penalty
  * a syndicated copy of a news story                           -> near-duplicate filtering
  * two outlets disagreeing on a plant's capacity (10 vs 12 GWh) -> conflict detection
  * company press releases with marketing claims                -> self-interest discount
  * an off-topic page                                           -> relevance filtering

    python scripts/build_benchmark.py
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "benchmark"

PAGES = [
    ("https://www.national-energy-board.gov/reports/sodium-ion-grid-storage-2025",
     "Sodium-ion Batteries for Grid Storage: 2025 Status Report", "2025-06-12",
     "The National Energy Board's 2025 status report finds that the global market for grid-scale sodium-ion "
     "batteries reached USD 1.8 billion in 2025, up from USD 0.6 billion in 2023. Cumulative grid deployments of "
     "sodium-ion storage reached 4.2 GWh by mid-2025. Average sodium-ion cell prices fell to USD 58 per kWh in 2025, "
     "compared with USD 72 per kWh for lithium iron phosphate (LFP) cells used in stationary storage. The report "
     "identifies three leading manufacturers by shipped capacity: Saltwave Cells, Voltaris Energy and Kestrel Grid. "
     "Sodium-ion chemistries avoid lithium, cobalt and nickel, and use aluminium current collectors on both electrodes, "
     "which lowers material cost. The board recommends sodium-ion for four-to-eight hour storage where weight matters less "
     "than cost."),
    ("https://www.reuters.com/business/energy/saltwave-opens-pune-gigafactory-2025-03-04",
     "Saltwave Cells opens sodium-ion gigafactory in Pune", "2025-03-04",
     "Saltwave Cells opened a sodium-ion cell factory in Pune with an annual capacity of 12 GWh in its first phase, the "
     "company said on Tuesday. The plant makes Saltwave the largest sodium-ion producer outside China by capacity. "
     "Rival Voltaris Energy raised USD 310 million in a Series C round in January 2025 to expand production in Chennai. "
     "Analysts said falling sodium-ion cell prices were winning orders from utilities for grid storage projects."),
    ("https://www.newswire-mirror.com/energy/saltwave-opens-pune-gigafactory",
     "Saltwave Cells opens sodium-ion gigafactory in Pune (syndicated)", "2025-03-04",
     "Saltwave Cells opened a sodium-ion cell factory in Pune with an annual capacity of 12 GWh in its first phase, the "
     "company said on Tuesday. The plant makes Saltwave the largest sodium-ion producer outside China by capacity. "
     "Rival Voltaris Energy raised USD 310 million in a Series C round in January 2025 to expand production in Chennai. "
     "Analysts said falling sodium-ion cell prices were winning orders from utilities for grid storage projects."),
    ("https://economictimes.indiatimes.com/industry/energy/saltwave-pune-plant-capacity",
     "Saltwave's Pune plant to start at 10 GWh, says state official", "2025-03-06",
     "A state industries department official said Saltwave Cells' Pune plant has an approved first-phase capacity of "
     "10 GWh, lower than the figure cited by the company, with expansion to 20 GWh planned by 2027. The plant qualifies "
     "for production-linked incentives for advanced chemistry cells. Saltwave did not respond to a request for comment "
     "on the capacity discrepancy."),
    ("https://www.iea.org/reports/stationary-storage-outlook-2025",
     "Stationary Storage Outlook 2025", "2025-09-01",
     "Sodium-ion batteries are projected to account for 12% of new stationary storage installations worldwide by 2030 "
     "in the stated policies scenario. Leading grid-scale sodium-ion cells now reach a cycle life of 6,000 cycles at 80% "
     "depth of discharge, comparable to LFP. The main constraint on growth is the supply of hard carbon anode material, "
     "which is currently produced by a small number of suppliers. Falling lithium carbonate prices since 2023 have "
     "narrowed the cost advantage of sodium-ion over LFP."),
    ("https://www.nature.com/articles/s41560-024-sodium-ion-review",
     "Sodium-ion batteries: performance, safety and materials (review)", "2024-11-20",
     "Commercial sodium-ion cells typically achieve gravimetric energy densities of 140 to 160 Wh/kg, below the 170 to "
     "200 Wh/kg of LFP cells. Sodium-ion cells can be stored and shipped at 0% state of charge, which removes the "
     "thermal runaway risk during transport. Under nail-penetration and overcharge tests, sodium-ion cells showed lower "
     "peak temperatures than LFP. Hard carbon remains the dominant anode material, and its cost depends on precursor "
     "biomass and pyrolysis energy."),
    ("https://www.saltwavecells.com/press/worlds-largest-sodium-ion-plant",
     "Saltwave Cells launches the world's largest sodium-ion plant", "2025-03-04",
     "Saltwave Cells today launched the world's largest and most advanced sodium-ion battery plant. Our next-generation "
     "cells reach an industry-leading 175 Wh/kg and will transform energy storage forever. The plant has a capacity of "
     "12 GWh. Saltwave is proud to lead the sodium revolution."),
    ("https://www.voltaris.energy/newsroom/gridco-500mwh-contract",
     "Voltaris Energy signs 500 MWh supply contract with GridCo", "2025-07-15",
     "Voltaris Energy signed a contract to supply 500 MWh of sodium-ion battery systems to GridCo for four-hour storage "
     "projects to be commissioned in 2026. Voltaris says its systems are designed for 20-year project lifetimes."),
    ("https://techcrunch.com/2024/10/02/kestrel-grid-sodium-ion-utility-partnership",
     "Kestrel Grid lands utility partnership for sodium-ion storage", "2024-10-02",
     "Kestrel Grid, a startup building containerised sodium-ion storage systems, announced a partnership with a regional "
     "utility to deploy 200 MWh of storage. Kestrel's founders previously built LFP systems and say sodium-ion's "
     "performance in cold weather, retaining over 90% capacity at minus 20 degrees Celsius, is a key selling point."),
    ("https://top-10-battery-stocks-to-buy.blog/sodium-ion-boom",
     "Top 10 Sodium-Ion Battery Stocks To Buy NOW (Huge Boom!)", "2025-08-01",
     "The sodium-ion battery market is exploding and was already worth USD 6 billion in 2025, according to our research. "
     "Sodium-ion batteries are 80% cheaper than lithium batteries. These top 10 stocks could make you rich. Saltwave "
     "Cells is our number one pick."),
    ("https://www.batteryweekly-archive.com/2019/sodium-ion-costs",
     "Is sodium-ion ready? Costs remain high", "2019-05-10",
     "In 2019, sodium-ion cells cost around USD 120 per kWh in pilot production, well above lithium-ion. Few companies "
     "had moved beyond laboratory prototypes, and cycle life rarely exceeded 2,000 cycles."),
    ("https://gridthoughts.medium.com/the-quiet-risks-of-sodium-ion",
     "The quiet risks of sodium-ion", "2025-05-02",
     "Sodium-ion has real risks that the hype ignores. Hard carbon anode supply is concentrated, so a shortage could stall "
     "factory ramp-ups. Lower energy density means larger, heavier systems, which raises land and installation costs for "
     "some projects. And if lithium carbonate prices stay low, LFP could keep its cost edge in many markets."),
    ("https://www.worldbank.org/en/topic/energy/brief/storage-for-mini-grids-2025",
     "Battery storage for mini-grids in emerging markets", "2025-04-18",
     "Sodium is roughly 1,000 times more abundant in the Earth's crust than lithium, which reduces supply concentration "
     "risk for emerging-market buyers. Pilot mini-grid projects in East Africa and South Asia have begun using "
     "sodium-ion storage, citing tolerance of high ambient temperatures and lower fire risk."),
    ("https://www.bloomberg.com/news/2025-10-01/lithium-prices-and-sodium-ion",
     "Cheap lithium tests sodium-ion's pitch to utilities", "2025-10-01",
     "Lithium carbonate prices have fallen more than 70% from their 2022 peak, letting LFP storage system prices drop "
     "sharply and testing sodium-ion's cost advantage. Executives at Saltwave Cells and Voltaris Energy said utilities "
     "still value sodium-ion's safety and cold-weather performance."),
    ("https://en.wikipedia.org/wiki/Sodium-ion_battery",
     "Sodium-ion battery - Wikipedia", "2025-09-30",
     "A sodium-ion battery is a rechargeable battery that uses sodium ions as charge carriers. It works on the same "
     "rocking-chair principle as lithium-ion batteries. Common cathodes include layered oxides, polyanionic compounds and "
     "Prussian blue analogues, and hard carbon is the most common anode."),
    ("https://www.recyclingtoday-example.com/lithium-battery-recycling-plants",
     "New lithium battery recycling plants open in Europe", "2025-02-11",
     "Three lithium-ion battery recycling plants opened in Europe this year, recovering nickel, cobalt and lithium from "
     "end-of-life electric vehicle packs using hydrometallurgical processes."),
]

QUESTIONS = [
    {"id": "market",
     "question": "What is the state of the grid-scale sodium-ion battery market in 2025, and who are the leading companies?",
     "required_facts": ["1.8 billion", "4.2 GWh", "Saltwave", "Voltaris", "Kestrel"],
     "trap_facts": ["6 billion", "80% cheaper"]},
    {"id": "compare",
     "question": "How do sodium-ion batteries compare with LFP lithium batteries for grid storage on cost, energy density, cycle life and safety?",
     "required_facts": ["58", "72", "160", "6,000", "thermal runaway"],
     "trap_facts": ["120 per kWh", "175 Wh/kg"]},
    {"id": "outlook",
     "question": "What are the main risks and the outlook for sodium-ion grid storage through 2030?",
     "required_facts": ["12%", "hard carbon", "lithium carbonate"],
     "trap_facts": ["6 billion"]},
    {"id": "saltwave",
     "question": "How large is Saltwave Cells' Pune sodium-ion plant, and is the capacity figure reliable?",
     "required_facts": ["12 GWh", "10 GWh"],
     "trap_facts": []},
]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with (OUT / "corpus.jsonl").open("w") as f:
        for url, title, published, text in PAGES:
            f.write(json.dumps({"url": url, "title": title, "published": published, "text": text}) + "\n")
    with (OUT / "questions.jsonl").open("w") as f:
        for q in QUESTIONS:
            f.write(json.dumps(q) + "\n")
    print(f"wrote {len(PAGES)} pages and {len(QUESTIONS)} benchmark questions to {OUT}")


if __name__ == "__main__":
    main()
