import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { communityServers } from "@/lib/mock-data";
import { Star, Download } from "lucide-react";

export default function DiscoverPage() {
  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold">Discover</h1>
          <Badge variant="outline" className="text-muted-foreground">
            Sample data
          </Badge>
        </div>
        <p className="text-muted-foreground text-sm">
          Community MCP servers you can install and connect. The listings below are
          illustrative sample data.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {communityServers.map((s) => (
          <Card key={s.id} className="flex flex-col">
            <CardHeader className="pb-2">
              <CardTitle className="text-base">{s.name}</CardTitle>
              <p className="text-xs text-muted-foreground">by {s.author}</p>
            </CardHeader>
            <CardContent className="flex flex-1 flex-col justify-between gap-3">
              <p className="text-sm text-muted-foreground">{s.description}</p>
              <div className="flex flex-wrap gap-1">
                {s.tags.map((tag) => (
                  <Badge key={tag} variant="outline" className="text-xs">
                    {tag}
                  </Badge>
                ))}
              </div>
              <div className="flex items-center justify-between pt-2">
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  <span className="flex items-center gap-1">
                    <Star className="h-3 w-3" />
                    {s.stars}
                  </span>
                  <span className="flex items-center gap-1">
                    <Download className="h-3 w-3" />
                    {s.installs.toLocaleString()}
                  </span>
                </div>
                <Button size="sm" variant="outline">
                  Install
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
