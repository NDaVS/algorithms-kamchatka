import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import Dispatcher from "./pages/Dispatcher";
import Driver from "./pages/Driver";

function ToDispatcher() {
  const { search } = useLocation(); // keep ?mock=1
  return <Navigate to={{ pathname: "/dispatcher", search }} replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/dispatcher" element={<Dispatcher />} />
      <Route path="/driver" element={<Driver />} /> {/* /driver?v=П-01 */}
      <Route path="*" element={<ToDispatcher />} />
    </Routes>
  );
}
