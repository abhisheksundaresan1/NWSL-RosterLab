from itscalledsoccer.client import AmericanSoccerAnalysis

asa = AmericanSoccerAnalysis()
players = asa.get_players(leagues="nwsl")

print("season_name dtype:", players["season_name"].dtype)
print("Sample values:")
print(players["season_name"].head(10).tolist())
print("Unique types in column:", set(type(v).__name__ for v in players["season_name"]))
