import { useState } from "react";
import { useEffect, useRef } from "react";
import debounce from "lodash/debounce";
import Input from "@/components/ui/Input";
function FilterAmount({ filter, labelField }) {
  const [values1, setValues1] = useState(0);
  const [values2, setValues2] = useState(0);

  const initialRender = useRef(true);

  useEffect(() => {
    if (initialRender.current) {
      initialRender.current = false;
    } else {
      filter([values1, values2]);
    }
  }, [values1, values2]);

  const handleDebounceFn1 = (value) => setValues1(value);
  const debounceFn1 = debounce(handleDebounceFn1, 0);

  const handleDebounceFn2 = (value) => setValues2(value);
  const debounceFn2 = debounce(handleDebounceFn2, 0);

  return (
    <div className="pt-3 pb-3">
      <div className=" grid grid-cols-2 gap-4">
        <div>
          <div>
            <h5 className="mb-[7px] text-[12.5px] font-extrabold text-navy-600 dark:text-night-body">{labelField} من</h5>
            <Input
              id="outlined-basic"
              variant="outlined"
              value={values1}
              type="number"
              onChange={(e) => debounceFn1(e.target.value)}
              className="w-full border-radius-0"
              size="sm"
            />
          </div>
        </div>
        <div>
          <div>
            <h5 className="mb-[7px] text-[12.5px] font-extrabold text-navy-600 dark:text-night-body">الى</h5>
            <Input
              id="outlined-basic"
              variant="outlined"
              value={values2}
              type="number"
              onChange={(e) => debounceFn2(e.target.value)}
              className="w-full border-radius-0"
              size="sm"
            />
          </div>
        </div>
      </div>
    </div>
  );
}

export default FilterAmount;
