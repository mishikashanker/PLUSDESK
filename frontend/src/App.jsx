import { useEffect, useState } from "react"
import "./App.css"

function App() {
  const [name, setName] = useState("")
  const [completed, setCompleted] = useState("")
  const [workingOn, setWorkingOn] = useState("")
  const [blocker, setBlocker] = useState("")
  const [message, setMessage] = useState("")
  const [standups, setStandups] = useState([])
  const [digest, setDigest] = useState(null)

  const loadStandups = async () => {
    try {
      const response = await fetch("http://127.0.0.1:8000/standups")
      const data = await response.json()

      setStandups(data.standups)
    } catch (error) {
      console.log("Could not load standups.")
    }
  }
const loadDigest = async () => {
  try {
    const response = await fetch("http://127.0.0.1:8000/digest")
    const data = await response.json()

    setDigest(data)
  } catch (error) {
    console.log("Could not load digest.")
  }
}
  const submitStandup = async (event) => {
    event.preventDefault()

    try {
      const response = await fetch("http://127.0.0.1:8000/standup", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          name: name,
          completed: completed,
          working_on: workingOn,
          blocker: blocker,
        }),
      })

      const data = await response.json()

      if (response.ok) {
        setMessage(
          `Update submitted successfully! Update ID: ${data.update_id}`
        )

        setName("")
        setCompleted("")
        setWorkingOn("")
        setBlocker("")

        loadStandups()
      } else {
        setMessage("Something went wrong.")
      }
    } catch (error) {
      setMessage("Could not connect to PulseDesk backend.")
    }
  }

  useEffect(() => {
  loadStandups()
  loadDigest()
}, [])

  return (
    <div className="app">

      <header className="header">
        <div className="logo">
          Pulse<span>Desk</span>
        </div>

        <div className="header-text">
          Team Status Intelligence
        </div>
      </header>

      <main className="container">

        <section className="hero">
          <h1>Daily Team Pulse</h1>
          <p>
            Turn scattered daily updates into a clear, actionable team digest.
          </p>
        </section>

        <section className="card digest-card">

  <div className="digest-header">
    <div>
      <h2>Today's Team Digest</h2>
      <p>Automatically compiled from submitted team updates.</p>
    </div>

    {digest && (
      <div className="digest-stats">
        <div className="stat">
          <strong>{digest.total_updates}</strong>
          <span>Updates</span>
        </div>

        <div className="stat blocker-stat">
          <strong>{digest.total_blockers}</strong>
          <span>Blockers</span>
        </div>
      </div>
    )}
  </div>

  {digest && digest.blockers.length > 0 && (
    <div className="blocker-section">

      <h3>🚧 Active Blockers</h3>

      {digest.blockers.map((blocker) => (
        <div className="digest-blocker" key={blocker.update_id}>

          <div>
            <strong>{blocker.name}</strong>

            <p>{blocker.blocker}</p>
          </div>

          <span className="source-id">
            Source #{blocker.update_id}
          </span>

        </div>
      ))}

    </div>
  )}

  {digest && digest.total_blockers === 0 && (
    <div className="no-blockers">
      ✓ No blockers reported today.
    </div>
  )}

</section>

        <section className="card">
          <h2>Submit Daily Update</h2>

          <form onSubmit={submitStandup}>

            <div className="form-grid">

              <div className="form-group">
                <label>Your Name</label>

                <input
                  type="text"
                  placeholder="e.g. Mishika"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  required
                />
              </div>

              <div className="form-group">
                <label>What did you complete?</label>

                <input
                  type="text"
                  placeholder="e.g. Finished login page"
                  value={completed}
                  onChange={(event) => setCompleted(event.target.value)}
                  required
                />
              </div>

              <div className="form-group">
                <label>What are you working on?</label>

                <input
                  type="text"
                  placeholder="e.g. Connecting API"
                  value={workingOn}
                  onChange={(event) => setWorkingOn(event.target.value)}
                  required
                />
              </div>

              <div className="form-group">
                <label>Any blockers?</label>

                <input
                  type="text"
                  placeholder="e.g. API returning 401"
                  value={blocker}
                  onChange={(event) => setBlocker(event.target.value)}
                />
              </div>

            </div>

            <button type="submit">
              Submit Daily Update
            </button>

          </form>

          {message && (
            <p className="success">
              {message}
            </p>
          )}
        </section>

        <section className="card">

          <h2>Team Updates</h2>

          <div className="updates">

            {standups.length === 0 ? (
              <p>No updates submitted yet.</p>
            ) : (
              standups.map((standup) => (

                <div className="update-card" key={standup.id}>

                  <div className="update-header">

                    <h3>
                      {standup.name || "Unknown Member"}
                    </h3>

                    <span className="update-id">
                      Update #{standup.id}
                    </span>

                  </div>

                  <div className="update-row">
                    <strong>Completed:</strong>
                    {standup.completed}
                  </div>

                  <div className="update-row">
                    <strong>Working on:</strong>
                    {standup.working_on}
                  </div>

                  <div className="update-row">
                    <strong>Blocker:</strong>

                    {standup.blocker ? (
                      <span className="blocker">
                        {standup.blocker}
                      </span>
                    ) : (
                      <span className="no-blocker">
                        No blockers
                      </span>
                    )}

                  </div>

                </div>

              ))
            )}

          </div>

        </section>

      </main>

    </div>
  )
}

export default App