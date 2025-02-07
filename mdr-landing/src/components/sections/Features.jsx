import React from "react";
import {
  AuthenticIcon,
  FreeIcon,
  MultilingualIcon,
  PersonalGrowthIcon,
  RegularUpdatesIcon,
  UserFriendlyIcon,
} from "../Icons";
import FeatureCard from "../ui/FeatureCard";

const Features = () => (
  <section id="features" className="py-20 bg-slate-700">
    <div className="container mx-auto px-4">
      <h2 className="text-2xl md:text-4xl font-bold text-center mb-12 text-white">
        Key Features
      </h2>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
        <FeatureCard
          feature={{
            icon: <FreeIcon />,
            title: "100% Free",
            description:
              "Access all resources completely free of charge, making Islamic knowledge accessible to everyone",
          }}
        />
        <FeatureCard
          feature={{
            icon: <AuthenticIcon />,
            title: "Authentic Sources",
            description:
              "All content is verified from authentic Islamic sources and scholarly interpretations",
          }}
        />
        <FeatureCard
          feature={{
            icon: <PersonalGrowthIcon />,
            title: "Personal Growth",
            description:
              "Enhance your Islamic knowledge at your own pace for personal development and spiritual growth",
          }}
        />
        <FeatureCard
          feature={{
            icon: <MultilingualIcon />,
            title: "Multilingual Support",
            description:
              "Access content in multiple languages to better understand Islamic teachings",
          }}
        />
        <FeatureCard
          feature={{
            icon: <UserFriendlyIcon />,
            title: "User-Friendly Interface",
            description:
              "Easy-to-navigate platform designed for seamless learning experience",
          }}
        />
        <FeatureCard
          feature={{
            icon: <RegularUpdatesIcon />,
            title: "Regular Updates",
            description:
              "Continuously updated content to provide comprehensive Islamic knowledge",
          }}
        />
      </div>
    </div>
  </section>
);

export default Features;
