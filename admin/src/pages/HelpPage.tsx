import { useEffect, type ReactNode } from 'react'
import { PageHeader } from '../components'

/** Reference for how the bot decides things. Every "How this works" link in the dashboard lands
 *  on one of these sections. Written from what the code does, so update it when that changes. */
const SECTIONS: { id: string; title: string; body: ReactNode }[] = [
  {
    id: 'scans',
    title: 'Engagement scans',
    body: <>
      <p>A scan looks at the posts made by the two tracked accounts inside the window you pick, and collects what linked members did with them:</p>
      <ul>
        <li><strong>Replies</strong>, from each post's reply list, plus a search for replies X hides from that list.</li>
        <li><strong>Quotes</strong> of tracked posts.</li>
        <li><strong>Retweets</strong>, dated when the post was made.</li>
        <li><strong>Mentions</strong>: standalone tweets that tag either account.</li>
      </ul>
      <p><strong>Deep check</strong> additionally reads every member's own timeline, which catches replies X hides everywhere else. It costs far more, so it is off unless you tick it, and it switches itself off again for the next scan.</p>
      <p>A scan never reaches back past the start of the current cycle. A window longer than the cycle simply covers the whole cycle, so points from before the last reset can never be counted twice. You can pick a preset window or an exact number of days.</p>
      <p>If a tweet that earned points is deleted, a later scan removes those points once X confirms it is gone. Replies found by the hidden-reply search or by a single-member scan keep their points even though X leaves them out of reply lists.</p>
    </>,
  },
  {
    id: 'member-scan',
    title: 'Scanning one member',
    body: <>
      <p>Scan this member, in the member's drawer, reads that person's own timeline, replies included, back to the window start or the number of tweets you choose. If X's timeline feed stops early, it fills the gap with a search of their tweets.</p>
      <p>It counts replies to either tracked account, standalone tweets that tag either account, and quotes of posts in Tracked posts. It does <strong>not</strong> count retweets; those only come from the normal scan. A quote of a post that is not in Tracked posts does not count either.</p>
    </>,
  },
  {
    id: 'diagnose',
    title: 'Why is a tweet not counted?',
    body: <>
      <p>The button on the Activity log looks one tweet up on X and works out which rule stopped it from scoring. The answers it can give:</p>
      <ul>
        <li>X no longer returns the tweet: deleted, a suspended or private account, or a wrong link.</li>
        <li>The author's X account is not linked to any active member.</li>
        <li>The author is protected and the scan skipped protected members.</li>
        <li>A standalone post names the account without the @, so it is not a mention.</li>
        <li>It replies to an account that is not tracked, or to a tracked post no scan window has reached yet.</li>
        <li>X hides it from the reply list or the mention feed. Only a single-member scan reads from the author's side and can still find it.</li>
      </ul>
      <p>When it is already counted, it shows the action and the points it earned.</p>
      <p>It only reads. It stops as soon as it has an answer, so it usually costs a fraction of a cent.</p>
    </>,
  },
  {
    id: 'sync',
    title: 'Sync from Discord',
    body: <>
      <p>Sync reads the whole member list of the server and brings the registry in line with it:</p>
      <ol>
        <li>Registers anyone who is missing. Bots, and the Discord IDs on the <strong>sync_ignored_discord_ids</strong> list on Scoring rules, are skipped.</li>
        <li>Updates changed Discord handles and fills in join dates.</li>
        <li>Sets protection from the roles listed in <strong>protected_role_names</strong>, in both directions: gaining one of those roles protects someone, losing it removes that protection.</li>
        <li>Marks anyone who is no longer in the server as inactive. Their points and history are kept.</li>
      </ol>
      <p>It never changes anything in Discord and uses no X credits. If Discord returns far fewer members than the registry holds, which is what a cut-short member list looks like, it deactivates nobody and says so.</p>
      <p>Members who are back in the server but still marked inactive are listed, not reactivated, in case you deactivated them yourself.</p>
    </>,
  },
  {
    id: 'protection',
    title: 'Protection',
    body: <>
      <p>A protected member never appears on the low-activity report, and scans leave them out whenever <strong>skip protected members</strong> is ticked. Nothing about protection is visible in Discord.</p>
      <p>Protection comes from two places, and the member drawer says which:</p>
      <ul>
        <li><strong>By a Discord role</strong>: holding one of the roles in <strong>protected_role_names</strong>. Sync owns this completely. Remove the role in Discord and the protection goes at the next sync.</li>
        <li><strong>By hand</strong>: the Protect button, an edit, or the protected column of an imported sheet. Sync never touches it.</li>
      </ul>
      <p>Role names must match exactly, emoji included: <em>Nucleus</em> and <em>Nucleus ✅</em> are different roles. When a configured name matches no role, the sync result tells you and suggests the closest ones.</p>
    </>,
  },
  {
    id: 'low-activity',
    title: 'The low-activity report',
    body: <>
      <p>The report lists active members at or below the <strong>low_activity_threshold</strong> on Scoring rules. You can try a different number for one view without saving it.</p>
      <p>Protected members and anyone who joined within <strong>newcomer_grace_days</strong> are always left out, and the banner says how many.</p>
      <p>It splits members in two. Those who linked an X account and scored little are evidence of low activity. Those who never linked one could not score at all, so their 0 means nothing on its own.</p>
      <p>Every row opens that member's full history, so you can check the evidence before you act on it.</p>
    </>,
  },
  {
    id: 'purge',
    title: 'Purge',
    body: <>
      <p><strong>The bot never removes anyone from the server.</strong> It has no permission to kick and does not need one. The purge on the low-activity report gives the ticked members a Discord role, such as Purge incoming, so you can find them and remove them in Discord yourself.</p>
      <p>Quiet members start ticked and members who never linked an X account start unticked. Untick anyone you want to spare. Preview shows exactly who gets the role before anything happens.</p>
      <p>Anyone who holds a role you chose to leave alone, Server Booster by default, is checked again at the moment you apply and skipped. If Discord will not say which roles someone holds, that person is left out rather than risked.</p>
      <p>The bot needs the <strong>Manage Roles</strong> permission, and its own role has to sit above the role it gives.</p>
    </>,
  },
  {
    id: 'follows',
    title: 'Follow check',
    body: <>
      <p>Check follows, on the Members page, reads the follower list of each tracked account once and matches every linked member against it. That is one pass rather than one lookup per member, and it costs about a cent per thousand followers read. The cost is shown before it runs.</p>
      <p>A member is recorded as not following only when the whole follower list was read. If a list is too long to finish, anyone not found in it stays <strong>not checked</strong>, never marked as not following, so an incomplete read cannot get someone purged.</p>
    </>,
  },
  {
    id: 'points',
    title: 'Points and adjustments',
    body: <>
      <p>Each counted action earns the points set on Scoring rules for its type and for which account it engaged with, plus any quality bonus. Only a limited number of actions per member per day are scored, set by <strong>daily_scored_action_cap</strong>. The Activity log shows the sum behind every award.</p>
      <p>You can add, take away or transfer points in the member drawer. Every adjustment is logged with its reason. To undo one, make the opposite adjustment.</p>
      <p>Changing a rule on Scoring rules recalculates every action in the current cycle straight away.</p>
    </>,
  },
  {
    id: 'cycles',
    title: 'Cycles and resets',
    body: <>
      <p>Resetting the leaderboard freezes everyone's standing into Scan reports, then sets every score to zero and starts a new cycle. Nothing is deleted: the frozen table and every scan report stay forever.</p>
      <p>From the reset onwards, scans only look back as far as the reset, whatever window you choose.</p>
    </>,
  },
  {
    id: 'members',
    title: 'Deactivating and deleting',
    body: <>
      <p><strong>Deactivate</strong> parks a member. They leave the leaderboard, the report and future scans, and reactivating restores everything. Sync deactivates people who left the server.</p>
      <p><strong>Delete record</strong> erases the member, their scored actions and their adjustments. Use it for records that should not exist, such as an admin's own account or an alt. Frozen tables from closed cycles and the Audit trail keep their copy. By default the ID is added to <strong>sync_ignored_discord_ids</strong> so the next sync does not bring the record back.</p>
    </>,
  },
  {
    id: 'costs',
    title: 'What things cost',
    body: <>
      <p>X data comes from twitterapi.io. 100,000 credits cost $1. Tweets, replies and profiles cost about 15 credits each; followers cost 1 credit each.</p>
      <p>Scans, X account checks, the follow check and single-member scans show their cost before they run. Sync, the reports, and anything done in Discord use no credits. The sidebar shows what this month has cost so far.</p>
    </>,
  },
  {
    id: 'discord',
    title: 'Commands in Discord',
    body: <>
      <p>For everyone: <code>/link-twitter</code>, <code>/unlink-twitter</code>, <code>/leaderboard</code>, <code>/my-score</code>, <code>/my-history</code>.</p>
      <p>For admins: <code>/check-engagement</code>, <code>/refresh-engagement</code>, <code>/scan-member</code>, <code>/track-post</code>, <code>/user-history</code>, <code>/adjust-points</code>, <code>/transfer-points</code>, <code>/low-activity-report</code>, <code>/reset-leaderboard</code>, <code>/sync-database</code>.</p>
    </>,
  },
]

export default function HelpPage() {
  // #help?topic=sync scrolls to that section, so a "How this works" link lands on the answer.
  useEffect(() => {
    const jump = () => {
      const topic = new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('topic')
      if (!topic) { window.scrollTo({ top: 0 }); return }
      const target = document.getElementById(`help-${topic}`)
      if (!target) return
      target.scrollIntoView({ block: 'start' })
      target.classList.remove('flash')
      void target.offsetWidth
      target.classList.add('flash')
    }
    jump()
    window.addEventListener('hashchange', jump)
    return () => window.removeEventListener('hashchange', jump)
  }, [])

  return <div className="page help-page">
    <PageHeader title="How it works" copy="What the bot counts, what each button does, and what it will never do. Every How this works link in the dashboard lands on one of these sections." />
    <div className="help-layout">
      <nav className="panel help-index" aria-label="Sections">
        {SECTIONS.map((section) => <a key={section.id} href={`#help?topic=${section.id}`}>{section.title}</a>)}
      </nav>
      <div className="help-sections">
        {SECTIONS.map((section) => <section className="panel help-section" id={`help-${section.id}`} key={section.id}>
          <h2>{section.title}</h2>
          {section.body}
        </section>)}
      </div>
    </div>
  </div>
}
