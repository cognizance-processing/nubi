/**
 * BillingUnavailable — what /billing shows when the billing feature is off
 * (open-source build, or an instance without billing enabled). An explanation
 * and a way onward instead of a blank sheet.
 */
import { Link } from 'react-router-dom'
import EmptyState from '../ui/EmptyState.jsx'

export default function BillingUnavailable() {
  return (
    <div style={{ padding: '48px 24px', maxWidth: 720, margin: '0 auto' }}>
      <h1 className="sr-only">Billing</h1>
      <EmptyState
        kind="gated"
        label="BILLING"
        title="Billing is not enabled on this instance"
        description="This workspace does not meter or invoice usage. You can still see how much of each quota has been used."
        action={<Link to="/settings/usage" className="nubi-btn nubi-btn-primary nubi-btn-md">View usage</Link>}
        secondaryAction={<Link to="/home" className="nubi-btn nubi-btn-secondary nubi-btn-md">Back to Home</Link>}
      />
    </div>
  )
}
