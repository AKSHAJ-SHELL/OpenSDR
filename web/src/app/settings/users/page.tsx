import { api } from "@/lib/api";
import { getSession } from "@/lib/session";
import { InviteUserForm } from "@/components/users/InviteUserForm";
import { UserRowActions } from "@/components/users/UserRowActions";
import { ApiDown } from "@/components/ui/ApiDown";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";

export const dynamic = "force-dynamic";

const SUBTITLE = "Who can sign in, and what their role permits. Owner-only.";

function fmtDate(iso: string | null): string {
  return iso ? iso.slice(0, 16).replace("T", " ") : "—";
}

export default async function UsersPage() {
  const session = await getSession();

  if (!session || session.role !== "owner") {
    return (
      <>
        <PageHeader title="Users" subtitle={SUBTITLE} />
        <EmptyState
          title="Requires an owner"
          body="Managing users — inviting, changing roles, disabling accounts — is limited to owner sessions. Ask an owner to make the change, or sign in as one."
        />
      </>
    );
  }

  let users;
  try {
    users = await api.users();
  } catch (e) {
    return (
      <>
        <PageHeader title="Users" subtitle={SUBTITLE} />
        <ApiDown error={e instanceof Error ? e.message : String(e)} />
      </>
    );
  }

  return (
    <>
      <PageHeader title="Users" subtitle={SUBTITLE} />

      <div className="grid gap-5 animate-rise-delay-1">
        <InviteUserForm />

        {users.length === 0 ? (
          <EmptyState
            title="No users yet"
            body="Invite the first user above. Until then, only the break-glass admin password can sign in."
          />
        ) : (
          <div className="overflow-visible rounded-[var(--radius)] border border-line bg-surface shadow-[var(--shadow)]">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-line bg-bg/70 text-[11px] uppercase tracking-[0.08em] text-faint">
                <tr>
                  <th className="px-5 py-3 font-semibold">User</th>
                  <th className="px-5 py-3 font-semibold">Role</th>
                  <th className="px-5 py-3 font-semibold">SSO</th>
                  <th className="px-5 py-3 font-semibold">Status</th>
                  <th className="px-5 py-3 font-semibold">Last login</th>
                  <th className="px-5 py-3 text-right font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => (
                  <tr
                    key={user.id}
                    className="border-b border-line last:border-0 transition-colors hover:bg-bg/80"
                  >
                    <td className="px-5 py-3.5">
                      <div className="font-semibold text-ink">
                        {user.display_name || user.email}
                      </div>
                      <div className="text-xs text-muted">{user.email}</div>
                    </td>
                    <td className="px-5 py-3.5">
                      <Badge tone={user.role === "owner" ? "accent" : "info"}>
                        {user.role}
                      </Badge>
                    </td>
                    <td className="px-5 py-3.5">
                      <Badge tone={user.sso_linked ? "good" : "muted"}>
                        {user.sso_linked ? "linked" : "no"}
                      </Badge>
                    </td>
                    <td className="px-5 py-3.5">
                      <Badge tone={user.disabled_at ? "warn" : "good"}>
                        {user.disabled_at ? "disabled" : "active"}
                      </Badge>
                    </td>
                    <td className="px-5 py-3.5 text-xs text-muted">
                      {fmtDate(user.last_login_at)}
                    </td>
                    <td className="px-5 py-3.5">
                      <UserRowActions user={user} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
