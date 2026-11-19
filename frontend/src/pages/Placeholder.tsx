import { useParams } from 'react-router-dom';

const NAMES: Record<string, string> = { pos: 'Point of sale', msea: 'MSEA students', onl: 'Online orders', pur: 'Purchasing', cash: 'Cash custody', acc: 'Accounts', rep: 'Reports' };

export default function Placeholder() {
  const { module = '' } = useParams();
  return (
    <>
      <h1>{NAMES[module] || 'Module'}</h1>
      <section className="panel">
        <p>The database tables, roles and permissions for this module are installed. Its screens are delivered in the next release on this foundation;
          until then the approved prototype remains the reference for how it works.</p>
      </section>
    </>
  );
}
