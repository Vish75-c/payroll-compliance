import React, { useEffect, useState } from "react";
import { api, onSessionExpired } from "../../shared/api.js";
import { go, useHashRoute } from "../../shared/routing.js";
import { Banner, Loading } from "../../shared/ui.jsx";
import LandingPage from "../landing/LandingPage.jsx";
import Login from "./Login.jsx";

const signedOut = { authenticated: false };

export default function SessionGate({ children }) {
  const route = useHashRoute();
  const [session, setSession] = useState(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    const unsubscribe = onSessionExpired(() => {
      setNotice("Your session has ended. Sign in again to continue.");
      setSession(signedOut);
    });
    api.session()
      .then((current) => { if (active) setSession(current); })
      .catch((err) => { if (active) setError(err.message); });
    return () => {
      active = false;
      unsubscribe();
    };
  }, []);

  function signIn(current, next) {
    setNotice("");
    setSession(current);
    if (next) go(next);
    else if (route === "/sign-in") go("/");
  }

  async function signOut() {
    await api.signOut();
    setNotice("");
    setSession(signedOut);
    go("/");
  }

  if (route === "/welcome" || (route === "/" && !notice && (error || session?.authenticated === false))) {
    return <LandingPage signedIn={session?.authenticated} />;
  }
  if (error) return <div className="main"><Banner kind="bad" title="Cannot check your session.">{error}</Banner></div>;
  if (!session) return <Loading />;
  if (!session.authenticated) return <Login onDone={signIn} notice={notice} />;
  return children(session, signOut, route);
}
