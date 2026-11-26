import React from "react";
import mark from "./assets/prosperr-mark.png";

export const PRODUCT_NAME = "Prosperr.io";
export { mark as brandMark };

export default function Brand() {
  return (
    <span className="product-brand">
      <span className="product-mark has-logo" aria-hidden="true"><img src={mark} alt="" /></span> {PRODUCT_NAME}
    </span>
  );
}
