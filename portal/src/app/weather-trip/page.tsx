"use client";

// Weather Trip Planner — the public example of Sealfleet's core loop:
// run a pipeline (gather → score), then visualize its output.
// Backed by the v2 pipeline `weather_trip_planner` and `weather-trip-mcp`.

import { useCallback, useState } from "react";
import {
  CloudSun, Loader2, MapPin, Play, Plus, Sun, Thermometer, Trophy, Wind, X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface Day {
  date: string;
  is_forecast: boolean;
  temp_max_c: number | null;
  temp_min_c: number | null;
  sunshine_hours: number;
  precipitation_mm: number | null;
  precipitation_probability: number | null;
  wind_max_kph: number | null;
}

interface CityWeather {
  name: string;
  country: string;
  source: string;
  days: Day[];
}

interface CityRank {
  city: string;
  country: string;
  score: number;
  perfect_days: number;
  avg_temp_max_c: number | null;
  avg_sunshine_hours: number;
  max_wind_kph: number | null;
  day_scores: number[];
}

interface PipelineOutput {
  weather: { past_days: number; forecast_days: number; cities: CityWeather[] };
  ranking: { ranking: CityRank[]; best_city: string | null; summary: string | null };
}

const DEFAULT_CITIES = ["Stockholm", "Barcelona", "Lisbon", "Rome", "Nice"];

function scoreColor(score: number): string {
  if (score >= 0.8) return "bg-green-500/70";
  if (score >= 0.6) return "bg-lime-500/60";
  if (score >= 0.4) return "bg-yellow-500/50";
  if (score >= 0.2) return "bg-orange-500/50";
  return "bg-red-500/50";
}

function weekdayLetter(date: string): string {
  return ["S", "M", "T", "W", "T", "F", "S"][new Date(date + "T12:00:00Z").getUTCDay()];
}

/** Temperature line across past + forecast days, with a divider at "today". */
function TempSparkline({ days }: { days: Day[] }) {
  const temps = days.map((d) => d.temp_max_c).filter((t): t is number => t != null);
  if (temps.length < 2) return null;
  const min = Math.min(...temps);
  const max = Math.max(...temps);
  const range = Math.max(1, max - min);
  const w = 280;
  const h = 48;
  const stepX = w / (days.length - 1);
  const y = (t: number) => h - 6 - ((t - min) / range) * (h - 12);

  const firstForecast = days.findIndex((d) => d.is_forecast);
  const splitX = firstForecast > 0 ? (firstForecast - 0.5) * stepX : 0;

  const pointsFor = (subset: (d: Day, i: number) => boolean) =>
    days
      .map((d, i) => (d.temp_max_c != null && subset(d, i) ? `${i * stepX},${y(d.temp_max_c)}` : null))
      .filter(Boolean)
      .join(" ");

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-12" preserveAspectRatio="none">
      {splitX > 0 && (
        <line x1={splitX} y1={0} x2={splitX} y2={h} className="stroke-border" strokeDasharray="3 3" />
      )}
      <polyline
        points={pointsFor((d, i) => i <= firstForecast)}
        fill="none"
        className="stroke-muted-foreground/50"
        strokeWidth={1.5}
      />
      <polyline
        points={pointsFor((d, i) => firstForecast < 0 || i >= firstForecast)}
        fill="none"
        className="stroke-amber-400"
        strokeWidth={2}
      />
    </svg>
  );
}

/** One cell per day: weekday + max temp, forecast cells tinted by trip score. */
function DayStrip({ days, dayScores }: { days: Day[]; dayScores: number[] }) {
  const forecastIdx = days.findIndex((d) => d.is_forecast);
  return (
    <div className="flex gap-1">
      {days.map((d, i) => {
        const fi = forecastIdx >= 0 ? i - forecastIdx : -1;
        const score = fi >= 0 && fi < dayScores.length ? dayScores[fi] : null;
        const title = [
          d.date,
          d.temp_max_c != null ? `max ${d.temp_max_c}°C` : null,
          `${d.sunshine_hours}h sun`,
          d.wind_max_kph != null ? `wind ${d.wind_max_kph} km/h` : null,
          d.precipitation_mm ? `${d.precipitation_mm}mm rain` : null,
          score != null ? `score ${score}` : "past week",
        ]
          .filter(Boolean)
          .join(" · ");
        return (
          <div
            key={d.date}
            title={title}
            className={`flex-1 min-w-0 rounded-sm px-0.5 py-1 text-center ${
              score != null ? scoreColor(score) : "bg-muted/40"
            }`}
          >
            <div className="text-[9px] leading-none text-foreground/60">{weekdayLetter(d.date)}</div>
            <div className="text-[10px] font-semibold tabular-nums leading-tight">
              {d.temp_max_c != null ? Math.round(d.temp_max_c) : "–"}°
            </div>
          </div>
        );
      })}
    </div>
  );
}

function CityCard({
  rank, position, weather,
}: {
  rank: CityRank; position: number; weather?: CityWeather;
}) {
  const isBest = position === 1;
  return (
    <Card className={isBest ? "border-amber-400/50" : ""}>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center justify-between text-base">
          <span className="flex items-center gap-2">
            <span className="text-muted-foreground tabular-nums">#{position}</span>
            <MapPin className="h-4 w-4 text-muted-foreground" />
            {rank.city}
            <span className="text-xs font-normal text-muted-foreground">{rank.country}</span>
            {isBest && (
              <Badge className="bg-amber-500/20 text-amber-400 border-amber-500/30 gap-1">
                <Trophy className="h-3 w-3" /> Best pick
              </Badge>
            )}
          </span>
          <span className="text-2xl font-bold tabular-nums">
            {(rank.score * 100).toFixed(0)}
            <span className="text-xs font-normal text-muted-foreground">/100</span>
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <Sun className="h-3.5 w-3.5 text-amber-400" /> {rank.avg_sunshine_hours}h sun/day
          </span>
          <span className="flex items-center gap-1">
            <Thermometer className="h-3.5 w-3.5 text-red-400" /> avg max {rank.avg_temp_max_c ?? "–"}°C
          </span>
          <span className="flex items-center gap-1">
            <Wind className="h-3.5 w-3.5 text-sky-400" /> up to {rank.max_wind_kph ?? "–"} km/h
          </span>
          <span>{rank.perfect_days} near-perfect day(s)</span>
        </div>
        {weather && (
          <>
            <TempSparkline days={weather.days} />
            <DayStrip days={weather.days} dayScores={rank.day_scores} />
            <div className="flex justify-between text-[10px] text-muted-foreground">
              <span>past week</span>
              <span>next {rank.day_scores.length} days · {weather.source}</span>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

export default function WeatherTripPage() {
  const [cities, setCities] = useState<string[]>(DEFAULT_CITIES);
  const [cityInput, setCityInput] = useState("");
  const [targetTemp, setTargetTemp] = useState(27);
  const [maxWind, setMaxWind] = useState(20);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<PipelineOutput | null>(null);
  const [traceId, setTraceId] = useState("");

  const addCity = () => {
    const c = cityInput.trim();
    if (c && !cities.some((x) => x.toLowerCase() === c.toLowerCase()) && cities.length < 8) {
      setCities([...cities, c]);
    }
    setCityInput("");
  };

  const run = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const r = await fetch("/api/weather-trip", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cities, target_temp_c: targetTemp, max_wind_kph: maxWind }),
      });
      const d = await r.json();
      if (!r.ok || !d.output) {
        setError(typeof d.error === "string" ? d.error : d.detail || "pipeline run failed");
        setResult(null);
      } else {
        setResult(d.output as PipelineOutput);
        setTraceId(d.trace_id || "");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "pipeline run failed");
    } finally {
      setLoading(false);
    }
  }, [cities, targetTemp, maxWind]);

  const weatherByCity = new Map(
    (result?.weather.cities ?? []).map((c) => [c.name, c] as const)
  );

  return (
    <div className="space-y-4 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <CloudSun className="h-6 w-6 text-amber-400" /> Weather Trip Planner
          </h1>
          <p className="text-sm text-muted-foreground">
            Example: a pipeline gathers each city&apos;s past week + next 10 days, scores them
            against your preferences, and this page visualizes the result.
          </p>
        </div>
        <Badge variant="outline" className="font-mono text-xs">weather_trip_planner</Badge>
      </div>

      <Card>
        <CardContent className="pt-6 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            {cities.map((c) => (
              <Badge key={c} variant="secondary" className="gap-1">
                {c}
                <button
                  aria-label={`remove ${c}`}
                  onClick={() => setCities(cities.filter((x) => x !== c))}
                  className="ml-0.5 hover:text-red-400"
                >
                  <X className="h-3 w-3" />
                </button>
              </Badge>
            ))}
            <div className="flex items-center gap-1">
              <Input
                value={cityInput}
                onChange={(e) => setCityInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addCity()}
                placeholder="Add city…"
                className="h-8 w-36"
              />
              <Button size="sm" variant="ghost" onClick={addCity} disabled={cities.length >= 8}>
                <Plus className="h-4 w-4" />
              </Button>
            </div>
          </div>
          <div className="flex flex-wrap items-end gap-4">
            <div className="space-y-1">
              <Label htmlFor="target-temp" className="text-xs flex items-center gap-1">
                <Thermometer className="h-3.5 w-3.5" /> Ideal max temp (°C)
              </Label>
              <Input
                id="target-temp"
                type="number"
                value={targetTemp}
                onChange={(e) => setTargetTemp(Number(e.target.value))}
                className="h-8 w-24"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="max-wind" className="text-xs flex items-center gap-1">
                <Wind className="h-3.5 w-3.5" /> Max wind (km/h)
              </Label>
              <Input
                id="max-wind"
                type="number"
                value={maxWind}
                onChange={(e) => setMaxWind(Number(e.target.value))}
                className="h-8 w-24"
              />
            </div>
            <Button onClick={run} disabled={loading || cities.length === 0} className="gap-2">
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              {loading ? "Running pipeline…" : "Run pipeline"}
            </Button>
          </div>
          {error && <p className="text-sm text-red-400">{error}</p>}
        </CardContent>
      </Card>

      {result?.ranking.summary && (
        <Card className="border-amber-400/30 bg-amber-500/5">
          <CardContent className="pt-4 pb-4 flex items-center gap-3">
            <Sun className="h-8 w-8 text-amber-400 shrink-0" />
            <div>
              <p className="font-medium">{result.ranking.summary}</p>
              {traceId && (
                <p className="text-xs text-muted-foreground font-mono">trace {traceId}</p>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {result && (
        <div className="grid gap-3 lg:grid-cols-2">
          {result.ranking.ranking.map((rank, i) => (
            <CityCard
              key={rank.city}
              rank={rank}
              position={i + 1}
              weather={weatherByCity.get(rank.city)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
