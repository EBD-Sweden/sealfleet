import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { dashboardStats, recentActivity } from "@/lib/mock-data";
import { Server, Wrench, Users, Activity, Clock, ShieldOff } from "lucide-react";
import { auth } from "@/auth";
import { Landing } from "@/components/landing";

const statCards = [
  { label: "Servers", value: dashboardStats.totalServers, icon: Server },
  { label: "Tools", value: dashboardStats.totalTools, icon: Wrench },
  { label: "Active Users", value: dashboardStats.activeUsers, icon: Users },
  { label: "Requests Today", value: dashboardStats.requestsToday.toLocaleString(), icon: Activity },
  { label: "Avg Latency", value: `${dashboardStats.avgLatencyMs}ms`, icon: Clock },
  { label: "Policy Denials", value: dashboardStats.policyDenials, icon: ShieldOff },
];

export default async function Home() {
  // Logged-out visitors (e.g. sealfleet.example.com) get the product landing;
  // authenticated users get the dashboard.
  const session = await auth();
  if (!session?.user) return <Landing />;
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold">Dashboard</h1>
        <Badge variant="outline" className="text-muted-foreground">
          Sample data
        </Badge>
      </div>
      <p className="text-sm text-muted-foreground">
        These figures are illustrative sample data, not live metrics.
      </p>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {statCards.map((stat) => (
          <Card key={stat.label}>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                {stat.label}
              </CardTitle>
              <stat.icon className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{stat.value}</div>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Recent Activity</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="space-y-3">
            {recentActivity.map((item, i) => (
              <li key={i} className="flex items-center justify-between text-sm">
                <span>{item.text}</span>
                <Badge variant="secondary" className="ml-2 shrink-0">
                  {item.time}
                </Badge>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
